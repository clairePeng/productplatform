"""记忆：可查看、可纠正、可清除。保存已确认的预审结论与口径，后续轮次复用。"""
from datetime import datetime

from . import db

DEFAULT_RULES_MEMORY = {
    "基准日": "2026-09-23（成立年限按周年计算）",
    "金额单位": "人民币万元",
    "期限口径": "只接受「个月」；天、年需先追问个月数",
    "用途口径": "与条款用词完全一致，流动资金≠周转资金",
    "名称口径": "企业名称须与 enterprise.enterprise_name 完全一致，不做模糊匹配",
    "主条件默认": "未说主条件时按利率优先",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def put(scope, key, value, source="agent"):
    with db.connect() as c:
        c.execute(
            "INSERT INTO memory(scope,key,value,source,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, source=excluded.source, updated_at=excluded.updated_at",
            (scope, key, value, source, _now()),
        )


def get(scope, key):
    with db.connect() as c:
        r = c.execute("SELECT value FROM memory WHERE scope=? AND key=?", (scope, key)).fetchone()
    return r["value"] if r else None


def list_all(scope=None):
    with db.connect() as c:
        if scope:
            rows = c.execute("SELECT scope,key,value,source,updated_at FROM memory WHERE scope=? ORDER BY id", (scope,))
        else:
            rows = c.execute("SELECT scope,key,value,source,updated_at FROM memory ORDER BY id")
        out = [dict(r) for r in rows]
    if scope in (None, "global"):
        have = {(m["scope"], m["key"]) for m in out}
        for k, v in DEFAULT_RULES_MEMORY.items():
            if ("global", k) not in have:
                out.insert(0, {"scope": "global", "key": k, "value": v, "source": "default", "updated_at": ""})
    return out


def delete(scope, key=None):
    with db.connect() as c:
        if key is None:
            return c.execute("DELETE FROM memory WHERE scope=?", (scope,)).rowcount
        return c.execute("DELETE FROM memory WHERE scope=? AND key=?", (scope, key)).rowcount


def clear_all():
    with db.connect() as c:
        return c.execute("DELETE FROM memory").rowcount
