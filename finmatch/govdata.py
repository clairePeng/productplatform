"""政务数据五类调取工具。授权闸门写在工具里：未授权时税务、社保、司法、历史融资一律不读。"""
import os

from . import config, db


class AuthorizationError(Exception):
    pass


class ToolFault(Exception):
    """模拟或真实的外部调用失败，供重试与降级使用。"""


def _fault(name):
    # FIN_FAULT=tax 或 tax:1（只失败一次）用于演示失败重试与降级
    spec = os.environ.get("FIN_FAULT", "")
    for item in filter(None, spec.split(",")):
        tool, _, n = item.partition(":")
        if tool == name:
            key = "_fault_left_" + name
            left = int(n) if n else 10 ** 9
            cur = _COUNTERS.get(key, left)
            if cur > 0:
                _COUNTERS[key] = cur - 1
                raise ToolFault("%s 调用失败（模拟）" % name)


_COUNTERS = {}


def reset_faults():
    _COUNTERS.clear()


def mask_value(col, v):
    if v in (None, ""):
        return v
    s = str(v)
    if col == "credit_code":
        return s[:4] + "*" * max(len(s) - 6, 0) + s[-2:]
    if col == "legal_representative":
        return s[0] + "*" * (len(s) - 1)
    return s


def _fact(table, col, value, key):
    return {"table": table, "column": col, "value": value, "source": "融资数据.sqlite", "key": key}


def _select_one(table, enterprise_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM %s WHERE enterprise_id=?" % table, (enterprise_id,)).fetchone()
    return dict(r) if r else None


def _auth_status(enterprise_id):
    with db.connect() as c:
        r = c.execute("SELECT data_authorization FROM enterprise WHERE enterprise_id=?", (enterprise_id,)).fetchone()
    return r["data_authorization"] if r else None


def _require_auth(enterprise_id):
    st = _auth_status(enterprise_id)
    if st != config.RULES["authorized_value"]:
        raise AuthorizationError("数据授权状态为「%s」，不得调取该企业的税务、社保、司法和历史融资" % (st if st else "空"))


def _block(table, row, enterprise_id):
    """把一行拆成：参与预审的事实 + 仅展示的字段（敏感字段脱敏）。"""
    wl = config.RULES["whitelist"].get(table, [])
    show = config.RULES["profile_display_only"].get(table, [])
    facts = {c: _fact(table, c, (row or {}).get(c), enterprise_id) for c in wl}
    display = {}
    for c in show:
        v = (row or {}).get(c)
        display[c] = _fact(table, c, mask_value(c, v), enterprise_id)
    return facts, display


def query_enterprise(name):
    _fault("enterprise")
    with db.connect() as c:
        r = c.execute("SELECT * FROM enterprise WHERE enterprise_name = ?", (name,)).fetchone()
    if not r:
        return {"found": False, "name": name}
    row = dict(r)
    facts, display = _block("enterprise", row, row["enterprise_id"])
    return {
        "found": True,
        "enterprise_id": row["enterprise_id"],
        "name": row["enterprise_name"],
        "authorized": row["data_authorization"] == config.RULES["authorized_value"],
        "facts": facts,
        "display": display,
    }


def query_tax(enterprise_id):
    _fault("tax")
    _require_auth(enterprise_id)
    row = _select_one("tax", enterprise_id)
    facts, display = _block("tax", row, enterprise_id)
    return {"row_exists": row is not None, "facts": facts, "display": display}


def query_social_security(enterprise_id):
    _fault("social_security")
    _require_auth(enterprise_id)
    row = _select_one("social_security", enterprise_id)
    facts, display = _block("social_security", row, enterprise_id)
    return {"row_exists": row is not None, "facts": facts, "display": display}


def query_judicial(enterprise_id):
    _fault("judicial")
    _require_auth(enterprise_id)
    row = _select_one("judicial", enterprise_id)
    facts, display = _block("judicial", row, enterprise_id)
    return {"row_exists": row is not None, "facts": facts, "display": display}


def query_financing_history(enterprise_id):
    _fault("financing_history")
    _require_auth(enterprise_id)
    with db.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM financing_history WHERE enterprise_id=? ORDER BY facility_id", (enterprise_id,))]
    vals = [r.get("overdue_unsettled") for r in rows]
    if any(v == 1 for v in vals):
        agg = 1
    elif any(v is None for v in vals):
        agg = None
    else:
        agg = 0
    facts = {"overdue_unsettled": _fact("financing_history", "overdue_unsettled", agg, enterprise_id)}
    items = []
    show = config.RULES["profile_display_only"]["financing_history"]
    for r in rows:
        item = {c: _fact("financing_history", c, r.get(c), r["facility_id"]) for c in show}
        item["overdue_unsettled"] = _fact("financing_history", "overdue_unsettled", r.get("overdue_unsettled"), r["facility_id"])
        items.append(item)
    return {"row_exists": bool(rows), "count": len(rows), "facts": facts, "items": items}


TOOLS = {
    "query_enterprise": query_enterprise,
    "query_tax": query_tax,
    "query_social_security": query_social_security,
    "query_judicial": query_judicial,
    "query_financing_history": query_financing_history,
}


def grant_authorization(enterprise_id, now):
    """记录企业授权：只写工作库（种子库只读），并留下授权编号、日期和范围。"""
    scope = "工商、税务、社保、司法、历史融资"
    with db.connect() as c:
        c.execute("UPDATE enterprise SET data_authorization=?, authorization_no=?, authorization_date=?, authorization_scope=? WHERE enterprise_id=?",
                  (config.RULES["authorized_value"], "AUTH-%s" % enterprise_id, now[:10], scope, enterprise_id))


def list_enterprise_names():
    with db.connect() as c:
        return [r["enterprise_name"] for r in c.execute("SELECT enterprise_name FROM enterprise")]


def distinct_enterprise_values(col):
    assert col in ("region", "industry")
    with db.connect() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT %s FROM enterprise WHERE %s IS NOT NULL" % (col, col))]
