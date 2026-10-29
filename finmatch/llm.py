"""DeepSeek 调用层。模型固定为 deepseek-v4.1-flash；提示词来自「提示词文档.md」。

模型做三件事：提议意图与工具（route）、补全规则没识别出的字段（extract）、用几句话解读已算好的预审结果（summary）。
模型只提议，不裁决：硬规则、授权、预审、排序都在代码里。
所有输出都要过代码校验，不通过或调用失败一律回退本地规则。
"""
import json
import os
import re
import time

from . import config
from .context import est_tokens, clip_list
from .tools import CURRENT

MODEL = "deepseek-flash"  # 接口名；展示名 DeepSeek-V4.1-Flash（deepseek-v4.1-flash）
DISPLAY = "deepseek-v4.1-flash"
TIMEOUT = 20
PROMPT_FILE = os.path.join(config.ROOT, "提示词文档.md")
# 成本为估算：元/百万 token，价格以官方为准，可用环境变量覆盖
PRICE_IN = float(os.environ.get("LLM_PRICE_IN", "1.0"))
PRICE_OUT = float(os.environ.get("LLM_PRICE_OUT", "2.0"))
MAX_REQUEST_TOKENS = 6000
BANNED = ("建议批准", "可以放款", "授信额度", "一定能", "批准", "放款")


def _load_env():
    path = os.path.join(config.ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env()


def prompt(name):
    with open(PROMPT_FILE, encoding="utf-8") as f:
        m = re.search(r"```prompt:%s\n(.*?)\n```" % re.escape(name), f.read(), re.S)
    if not m:
        raise KeyError("提示词文档缺少 prompt:%s" % name)
    return m.group(1).strip()


def enabled():
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def _transport(payload):
    """返回接口原始 JSON。测试里替换这里。"""
    import httpx
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    r = httpx.post(base + "/chat/completions", json=payload, timeout=TIMEOUT,
                   headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]})
    if r.status_code >= 400:
        raise RuntimeError("HTTP %d %s" % (r.status_code, r.text[:200]))
    return r.json()


def _record(kind, ok, ms, usage, est_in, est_out, error=None):
    pt = int((usage or {}).get("prompt_tokens") or est_in)
    ct = int((usage or {}).get("completion_tokens") or est_out)
    rec = {"kind": kind, "model": DISPLAY, "ok": ok, "ms": ms, "prompt_tokens": pt, "completion_tokens": ct,
           "usage_source": "api" if usage else "estimate", "cost_cny": round((pt * PRICE_IN + ct * PRICE_OUT) / 1e6, 6)}
    if error:
        rec["error"] = error[:200]
    tr = CURRENT.get()
    if tr is not None:
        tr.llm.append(rec)
    return rec


def _is_overflow(err):
    return bool(err) and bool(re.search(r"context|too long|maximum|token", err, re.I))


def _call(kind, system, user, json_mode=False):
    """返回 (文本, 错误)。无密钥时返回 (None, None) 且不记账。"""
    if not enabled():
        return None, None
    if est_tokens(system) + est_tokens(user) > MAX_REQUEST_TOKENS:
        _record(kind, False, 0, None, 0, 0, "overflow: 请求超过本地预算，未发送")
        return None, "overflow"
    payload = {"model": MODEL, "temperature": 0, "stream": False,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    t = time.time()
    try:
        data = _transport(payload)
        text = data["choices"][0]["message"]["content"] if isinstance(data, dict) else data
        usage = data.get("usage") if isinstance(data, dict) else None
        _record(kind, True, int((time.time() - t) * 1000), usage, est_tokens(system) + est_tokens(user), est_tokens(text or ""))
        return text, None
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        _record(kind, False, int((time.time() - t) * 1000), None, est_tokens(system) + est_tokens(user), 0, err)
        return None, err


def chat(system, user, json_mode=False, kind="chat"):
    """返回模型文本；未配置密钥或调用失败返回 None。"""
    return _call(kind, system, user, json_mode)[0]


def extract(text, known_company, db_names, vocab, skip=(), context=None):
    """补全字段。返回通过校验的 {字段: 值}，skip 里的字段（规则已判定的）不补。
    带会话上下文调用；上下文导致溢出时去掉上下文重试一次。"""
    body = {"text": text, "known_company": known_company}
    if context:
        body["context"] = context
    raw, err = _call("extract", prompt("extract"), json.dumps(body, ensure_ascii=False), json_mode=True)
    if raw is None and context and _is_overflow(err):
        body.pop("context")
        raw, err = _call("extract", prompt("extract"), json.dumps(body, ensure_ascii=False), json_mode=True)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    c = d.get("company")
    if "company" not in skip and isinstance(c, str) and c in db_names and c in text:
        out["company"] = c
    a = d.get("amount_wan")
    if "amount" not in skip and isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0 and re.search(r"[万亿元]", text):
        out["amount"] = float(a)
    t = d.get("term_months")
    if "term" not in skip and isinstance(t, int) and not isinstance(t, bool) and t > 0 and re.search(r"%d\s*个月" % t, text):
        out["term"] = t
    p = d.get("purpose")
    if "purpose" not in skip and isinstance(p, str) and p in vocab and p in text:
        out["purpose"] = p
    g = d.get("guarantee")
    if "guarantee" not in skip and g in ("信用", "抵押", "质押", "保证") and g in text:
        out["guarantee"] = g
    m = d.get("primary")
    if "primary" not in skip and m in ("利率优先", "额度优先"):
        out["primary"] = m
    return out


INTENTS = ("demand", "confirm", "abandon", "hold", "gov", "report", "profile", "memory_show", "recall", "other")
# 意图 → 允许使用的工具；模型提议的工具必须落在所提意图的允许范围内，否则被裁决丢弃
INTENT_TOOLS = {
    "demand": {"query_enterprise", "query_tax", "query_social_security", "query_judicial", "query_financing_history",
              "query_product_table", "rank_products", "save_solution"},
    "confirm": {"save_solution"}, "abandon": {"save_solution"}, "hold": set(),
    "gov": {"gov_view_stats"}, "report": {"gov_view_stats"}, "profile": {"query_enterprise"},
    "memory_show": {"memory_list"}, "recall": {"memory_list"}, "other": set(),
}


def route(text, state):
    """模型提议意图与工具。返回 {"intents": [...], "tools": [...], "reason": str, "dropped_tools": [...]}，失败返回 None。
    只做形式校验（取值在枚举内、工具在意图允许范围内）；是否执行由 agent 裁决。"""
    raw = chat(prompt("route"), json.dumps({"text": text, "state": state}, ensure_ascii=False), json_mode=True, kind="route")
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("intents"), list):
        return None
    intents = [i for i in d["intents"] if i in INTENTS]
    if not intents:
        return None
    allowed = set().union(*(INTENT_TOOLS[i] for i in intents))
    tools = [t for t in (d.get("tools") or []) if isinstance(t, str)]
    reason = d.get("reason") if isinstance(d.get("reason"), str) else ""
    return {"intents": intents, "tools": [t for t in tools if t in allowed],
            "dropped_tools": [t for t in tools if t not in allowed], "reason": reason[:60]}


def _numbers(s):
    return {float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)}


def summarize(view):
    """解读已算好的结果。校验不过返回 None。"""
    view = dict(view)
    view["rejected"] = clip_list(view.get("rejected"))
    src = json.dumps(view, ensure_ascii=False)
    raw = chat(prompt("summary"), src, kind="summary")
    if not raw:
        return None
    text = raw.strip()
    if not text or len(text) > 200 or any(b in text for b in BANNED):
        return None
    if not _numbers(text) <= _numbers(src):
        return None
    return text
