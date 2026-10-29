"""FastAPI 服务：六个页面对应的接口。启动：.venv/bin/python -m uvicorn finmatch.server:app --port 8080"""
import glob
import os

from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, llm, memory, obs, products as P, skills, solutions
from .agent import AGENT
from .extractor import ExtractionError

MAX_UPLOAD = 20 * 1024 * 1024

app = FastAPI(title="企业智能融资匹配平台")
db.init_db()

# 角色权限：(允许的路径前缀, 是否只读)。请求头 X-Role 指定角色，缺省按客户经理（兼容脚本）。
ROLES = {
    "manager": {"name": "客户经理", "rules": [("/api/products", False), ("/api/chat", False), ("/api/conversations", False),
                                           ("/api/sessions", False), ("/api/profile", False), ("/api/gov-view", True),
                                           ("/api/report", False), ("/api/solutions", False), ("/api/health", False),
                                           ("/api/traces", True), ("/api/metrics", True)]},
    "gov": {"name": "政府人员", "rules": [("/api/profile", True), ("/api/gov-view", True), ("/api/report", True),
                                        ("/api/solutions", True), ("/api/health", True)]},
}


@app.middleware("http")
async def role_guard(request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        role = request.headers.get("X-Role", "manager")
        cfg = ROLES.get(role)
        if not cfg:
            return JSONResponse(status_code=400, content={"error": "未知角色：%s" % role})
        ok = any(path == pre or path.startswith(pre + "/") for pre, ro in cfg["rules"]
                 if not ro or request.method in ("GET", "HEAD"))
        if not ok:
            return JSONResponse(status_code=403, content={"error": "当前角色（%s）无权访问该功能" % cfg["name"]})
    resp = await call_next(request)
    if path.startswith("/static/") or path == "/":
        resp.headers["Cache-Control"] = "no-cache"  # 前端脚本更新后刷新即生效
    return resp


class ChatIn(BaseModel):
    session_id: str
    message: str = ""


class MemoryIn(BaseModel):
    scope: str = "global"
    key: str
    value: str


@app.get("/api/health")
def health():
    return {"ok": True, "products": len(P.list_products()), "base_date": config.BASE_DATE.isoformat(),
            "model": "deepseek-v4.1-flash" if llm.enabled() else "未配置密钥，使用本地规则"}


# ---------- 产品管理 ----------
@app.post("/api/products/upload")
async def upload_product(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        return JSONResponse(status_code=413, content={"error": "文件超过 20MB，已拒绝"})
    try:
        prod, overwritten = P.ingest_pdf(data, file.filename or "")
    except ExtractionError as e:
        return JSONResponse(status_code=400, content={"error": e.message, "missing": e.missing, "stored": False})
    return {"product": prod, "overwritten": overwritten,
            "message": ("已覆盖产品 %s 的原有记录" if overwritten else "已入库产品 %s") % prod["product_code"]}


@app.post("/api/products/upload-samples")
def upload_samples():
    done, errors = [], []
    for f in sorted(glob.glob(os.path.join(config.PRODUCT_PDF_DIR, "*.pdf"))):
        try:
            with open(f, "rb") as fh:
                prod, ow = P.ingest_pdf(fh.read(), os.path.basename(f))
            done.append(prod["product_code"])
        except ExtractionError as e:
            errors.append({"file": os.path.basename(f), "error": e.message})
    return {"ingested": done, "errors": errors}


@app.get("/api/products")
def list_products():
    return {"products": P.list_products()}


@app.get("/api/products/export.csv")
def export_products():
    return PlainTextResponse("\ufeff" + P.export_csv_text(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=products.csv"})


@app.delete("/api/products/{code}")
def delete_product(code: str):
    if not P.delete_product(code):
        raise HTTPException(404, "产品不存在")
    return {"deleted": code}


# ---------- 对话 ----------
@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    sid = "".join(ch for ch in body.session_id if ch.isalnum() or ch in "-_")[:40] or "default"
    out = AGENT.handle(sid, body.message, request.headers.get("X-Role", "manager"))
    out["session_id"] = sid
    return out


class AuthIn(BaseModel):
    decision: str  # grant / deny


def _clean_sid(sid):
    return "".join(ch for ch in sid if ch.isalnum() or ch in "-_")[:40] or "default"


@app.get("/api/conversations")
def conversations():
    return {"conversations": AGENT.conversations()}


@app.post("/api/conversations")
def new_conversation():
    sid = AGENT.new_session()
    return {"session_id": sid, "title": AGENT.conversation(sid)["title"]}


@app.get("/api/conversations/{sid}")
def conversation(sid: str):
    return AGENT.conversation(_clean_sid(sid))


@app.post("/api/conversations/{sid}/authorize")
def authorize(sid: str, body: AuthIn):
    if body.decision not in ("grant", "deny"):
        raise HTTPException(400, "decision 只能是 grant 或 deny")
    sid = _clean_sid(sid)
    if not AGENT.conversation(sid)["pending_auth"]:
        raise HTTPException(409, "当前对话没有待授权的企业")
    out = AGENT.authorize(sid, body.decision)
    out["session_id"] = sid
    out.update({k: v for k, v in AGENT.conversation(sid).items() if k in ("pending_auth", "title", "solution")})
    return out


class ActionIn(BaseModel):
    action: str
    product_code: Optional[str] = None


@app.post("/api/conversations/{sid}/solution")
def solution_action(sid: str, body: ActionIn):
    if body.action not in ("confirm", "abandon"):
        raise HTTPException(400, "action 只能是 confirm / abandon")
    sid = _clean_sid(sid)
    return AGENT.solution_action(sid, body.action, body.product_code)


@app.get("/api/sessions")
def sessions():
    return {"sessions": AGENT.list_sessions()}


@app.get("/api/sessions/{sid}/messages")
def messages(sid: str):
    return {"messages": AGENT.history(sid)}


# ---------- 画像 ----------
@app.get("/api/profile")
def profile(name: str):
    return AGENT.profile(name)


# ---------- 方案 ----------
@app.get("/api/solutions")
def list_solutions(status: str = None):
    return {"solutions": solutions.list_solutions(status=status)}


@app.get("/api/solutions/{sid}")
def solution(sid: str):
    s = solutions.get(sid)
    if not s:
        raise HTTPException(404, "方案不存在")
    s["events"] = solutions.events(sid)
    return s


@app.post("/api/solutions/{sid}/confirm")
def confirm(sid: str):
    s = solutions.get(sid)
    status, changed = solutions.set_status(sid, solutions.CONFIRMED)
    if status is None:
        raise HTTPException(404, "方案不存在")
    return {"solution_id": sid, "status": status, "changed": changed}


@app.post("/api/solutions/{sid}/abandon")
def abandon(sid: str):
    status, changed = solutions.set_status(sid, solutions.ABANDONED)
    if status is None:
        raise HTTPException(404, "方案不存在")
    return {"solution_id": sid, "status": status, "changed": changed}


# ---------- 政府视图与报告 ----------
@app.get("/api/gov-view")
def gov_view():
    return solutions.stats()


@app.get("/api/report")
def report():
    return solutions.report()


# ---------- 记忆与技能 ----------
@app.get("/api/memory")
def memory_list():
    return {"items": memory.list_all()}


@app.put("/api/memory")
def memory_put(body: MemoryIn):
    memory.put(body.scope, body.key, body.value, "manager")
    return {"ok": True}


@app.delete("/api/memory")
def memory_delete(scope: str = None, key: str = None):
    if scope is None:
        return {"deleted": memory.clear_all()}
    return {"deleted": memory.delete(scope, key)}


@app.get("/api/skills")
def skill_list():
    return {"skills": list(skills.SKILLS.values())}


@app.post("/api/admin/reset")
def reset(confirm: bool = False):
    if not confirm:
        raise HTTPException(400, "需要 confirm=true")
    db.reset_runtime()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(config.STATIC_DIR, "index.html"))


@app.get("/api/traces")
def traces(session_id: str = None, status: str = None, limit: int = 50):
    return {"traces": obs.list_traces(_clean_sid(session_id) if session_id else None, status, limit)}


@app.get("/api/traces/{trace_id}")
def trace_detail(trace_id: str):
    t = obs.get_trace("".join(ch for ch in trace_id if ch.isalnum())[:32])
    if not t:
        raise HTTPException(404, "没有这条调用链")
    return t


@app.get("/api/metrics")
def metrics():
    return obs.metrics()
