"""可观测：调用链落库、按回合还原、聚合指标（耗时、token、成本、出错节点）。"""
from datetime import datetime

from . import db


def _status(t):
    if t.get("error"):
        return "error"
    if t.get("degraded") or any("降级" in n for n in t.get("notes", [])):
        return "degraded"
    return "ok"


def persist(session_id, t):
    """落库失败不影响业务回合。"""
    try:
        u = t.get("usage", {})
        with db.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO traces(trace_id,session_id,at,total_ms,prompt_tokens,completion_tokens,cost_cny,status,error_node,body) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (t["trace_id"], session_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), t.get("total_ms", 0),
                 u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cost_cny", 0), _status(t),
                 (t.get("error") or {}).get("node"), db.dumps(t)))
    except Exception:
        pass


def list_traces(session_id=None, status=None, limit=50):
    q, a = "SELECT trace_id,session_id,at,total_ms,prompt_tokens,completion_tokens,cost_cny,status,error_node FROM traces", []
    w = []
    if session_id:
        w.append("session_id=?"); a.append(session_id)
    if status:
        w.append("status=?"); a.append(status)
    if w:
        q += " WHERE " + " AND ".join(w)
    q += " ORDER BY at DESC, rowid DESC LIMIT ?"
    a.append(max(1, min(int(limit), 200)))
    with db.connect() as c:
        return [dict(r) for r in c.execute(q, a)]


def get_trace(trace_id):
    with db.connect() as c:
        r = c.execute("SELECT session_id,at,status,body FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    if not r:
        return None
    d = db.loads(r["body"], {})
    d.update({"session_id": r["session_id"], "at": r["at"], "status": r["status"]})
    return d


def metrics(limit=500):
    """最近 limit 个回合的聚合：成功/降级/出错、耗时分位、token、成本、各节点平均耗时、出错节点分布、模型调用成功率。"""
    with db.connect() as c:
        rows = c.execute("SELECT status,total_ms,prompt_tokens,completion_tokens,cost_cny,error_node,body FROM traces ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    n = len(rows)
    out = {"turns": n, "ok": 0, "degraded": 0, "error": 0, "errors_by_node": {}, "node_avg_ms": {}, "llm": {}}
    if not n:
        return out
    ms = sorted(r["total_ms"] for r in rows)
    node_ms, llm = {}, {}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
        if r["error_node"]:
            out["errors_by_node"][r["error_node"]] = out["errors_by_node"].get(r["error_node"], 0) + 1
        body = db.loads(r["body"], {})
        for sp in body.get("spans", []):
            node_ms.setdefault(sp["node"], []).append(sp.get("ms", 0))
        for call in body.get("llm", []):
            k = llm.setdefault(call["kind"], {"calls": 0, "failed": 0, "ms": 0})
            k["calls"] += 1
            k["failed"] += 0 if call.get("ok") else 1
            k["ms"] += call.get("ms", 0)
    out.update({
        "avg_ms": int(sum(ms) / n), "p50_ms": ms[n // 2], "p95_ms": ms[min(n - 1, int(n * 0.95))],
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows), "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "cost_cny": round(sum(r["cost_cny"] for r in rows), 6),
        "node_avg_ms": {k: int(sum(v) / len(v)) for k, v in node_ms.items()},
        "llm": {k: {"calls": v["calls"], "failed": v["failed"], "avg_ms": int(v["ms"] / v["calls"])} for k, v in llm.items()},
        "cost_note": "成本按 token 估算（元/百万 token，见 llm.py PRICE_IN/PRICE_OUT），以账单为准",
    })
    return out
