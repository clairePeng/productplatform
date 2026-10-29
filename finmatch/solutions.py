"""融资对接单（方案）：待确认 / 已确认 / 已放弃，以及政府视图和报告的统计口径。"""
from datetime import datetime

from . import db

PENDING, CONFIRMED, ABANDONED = "待确认", "已确认", "已放弃"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _to_dict(r, brief=False):
    d = dict(r)
    for k in ("request", "precheck", "recommended"):
        d[k] = db.loads(d.get(k))
    if brief:
        d.pop("precheck", None)
    d["confirmed_product"], d["confirmed_at"] = _confirmed_info(d)
    return d


def _confirmed_info(d):
    """确认的推荐名单与确认时间：仅已确认方案有值；无准入符合产品时名单为 None。"""
    if d.get("status") != CONFIRMED:
        return None, None
    names = [c["product_name"] for c in (d.get("recommended") or {}).get("cards", [])]
    return ("、".join(names) or None), d.get("decided_at")


def _event(c, sid, event, detail=""):
    c.execute("INSERT INTO solution_events(solution_id,event,detail,at) VALUES(?,?,?,?)", (sid, event, detail, _now()))


def create(session_id, enterprise, request, primary, precheck, ranking):
    with db.connect() as c:
        seq = c.execute("SELECT COUNT(*) FROM solutions WHERE session_id=?", (session_id,)).fetchone()[0] + 1
        sid = "SOL-%s-%03d" % (session_id, seq)
        ts = _now()
        c.execute(
            "INSERT INTO solutions(solution_id,session_id,seq,enterprise_id,enterprise_name,region,industry,request,primary_condition,"
            "conclusion,precheck,recommended,eligible_count,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, session_id, seq, enterprise["enterprise_id"], enterprise["name"], enterprise["region"], enterprise["industry"],
             db.dumps(request), primary, "已预审", db.dumps(precheck), db.dumps(ranking), ranking["eligible_count"], PENDING, ts, ts),
        )
        _event(c, sid, "创建", "预审完成，状态待确认")
    return sid


def update_precheck(sid, request, primary, precheck, ranking):
    with db.connect() as c:
        c.execute(
            "UPDATE solutions SET request=?, primary_condition=?, precheck=?, recommended=?, eligible_count=?, updated_at=? WHERE solution_id=?",
            (db.dumps(request), primary, db.dumps(precheck), db.dumps(ranking), ranking["eligible_count"], _now(), sid),
        )
        _event(c, sid, "条件变更", "重新预审，主条件 %s" % primary)


def hit_codes(s):
    """统计口径：每个准入符合的产品算一次命中（不限于名单前 3 个）；需人工复核和准入不符不计。"""
    return (s["recommended"] or {}).get("eligible_codes", [])


def get(sid):
    with db.connect() as c:
        r = c.execute("SELECT * FROM solutions WHERE solution_id=?", (sid,)).fetchone()
    return _to_dict(r) if r else None


def list_solutions(status=None, session_id=None):
    q, a = "SELECT * FROM solutions", []
    cond = []
    if status:
        cond.append("status=?")
        a.append(status)
    if session_id:
        cond.append("session_id=?")
        a.append(session_id)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY created_at, solution_id"
    with db.connect() as c:
        rows = c.execute(q, a).fetchall()
    return [_to_dict(r, brief=True) for r in rows]


def events(sid):
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT event,detail,at FROM solution_events WHERE solution_id=? ORDER BY id", (sid,))]


def set_status(sid, status):
    """待确认→已确认/已放弃；已确认与已放弃不再改变（返回当前状态与是否发生变化）。"""
    with db.connect() as c:
        r = c.execute("SELECT status FROM solutions WHERE solution_id=?", (sid,)).fetchone()
        if not r:
            return None, False
        if r["status"] != PENDING:
            return r["status"], False
        ts = _now()
        c.execute("UPDATE solutions SET status=?, updated_at=?, decided_at=? WHERE solution_id=?", (status, ts, ts, sid))
        _event(c, sid, status, "经理操作")
    return status, True


def _confirmed():
    with db.connect() as c:
        rows = c.execute("SELECT * FROM solutions WHERE status=? ORDER BY created_at, solution_id", (CONFIRMED,)).fetchall()
    return [_to_dict(r) for r in rows]


def _count(items):
    out = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return sorted(out.items(), key=lambda kv: -kv[1])  # 稳定排序，同数按首次出现


def stats():
    sols = _confirmed()
    n = len(sols)
    if n == 0:
        return {"confirmed_count": 0, "empty": True, "message": "暂无已确认方案"}
    from . import products as P
    names = {p["product_code"]: p["product_name"] for p in P.list_products()}
    prod_hits = []
    for s in sols:
        prod_hits.extend(hit_codes(s))
    matched = sum(1 for s in sols if s["eligible_count"] >= 1)
    return {
        "confirmed_count": n,
        "empty": False,
        "region_heat": [{"region": k, "count": v} for k, v in _count([s["region"] for s in sols])],
        "industry_distribution": [{"industry": k, "count": v} for k, v in _count([s["industry"] for s in sols])],
        "product_ranking": [{"product_code": k, "product_name": names.get(k, k), "count": v} for k, v in _count(prod_hits)],
        "match_rate": {"numerator": matched, "denominator": n, "text": "%d/%d" % (matched, n), "percent": round(matched * 100.0 / n, 1)},
        "solutions": [s["solution_id"] for s in sols],
    }


def report():
    """企业、产品、区域三节，口径与 stats() 相同，并列出对应的已确认方案。"""
    sols = _confirmed()
    st = stats()
    if st["empty"]:
        return {"empty": True, "message": "暂无已确认方案", "confirmed_count": 0}
    from . import products as P
    names = {p["product_code"]: p["product_name"] for p in P.list_products()}
    plan = lambda s: {"solution_id": s["solution_id"], "enterprise_name": s["enterprise_name"], "primary_condition": s["primary_condition"],
                      "eligible_count": s["eligible_count"], "decided_at": s["decided_at"]}
    def add(lst, x):
        if x not in lst:
            lst.append(x)
    ent = {}
    for s in sols:
        e = ent.setdefault(s["enterprise_name"], {"enterprise_name": s["enterprise_name"], "region": s["region"], "industry": s["industry"],
                                                  "eligible_products": [], "plans": []})
        e["plans"].append(plan(s))
        for code in hit_codes(s):
            add(e["eligible_products"], names.get(code, code))
    prod = {}
    for item in st["product_ranking"]:
        prod[item["product_code"]] = {"product_code": item["product_code"], "product_name": item["product_name"], "count": item["count"], "enterprises": [], "plans": []}
    for s in sols:
        for code in hit_codes(s):
            prod[code]["plans"].append(plan(s))
            add(prod[code]["enterprises"], s["enterprise_name"])
    reg = {}
    for item in st["region_heat"]:
        reg[item["region"]] = {"region": item["region"], "count": item["count"], "enterprises": [], "plans": []}
    for s in sols:
        reg[s["region"]]["plans"].append(plan(s))
        add(reg[s["region"]]["enterprises"], s["enterprise_name"])
    return {
        "empty": False,
        "confirmed_count": st["confirmed_count"],
        "match_rate": st["match_rate"],
        "enterprise_section": list(ent.values()),
        "product_section": list(prod.values()),
        "region_section": list(reg.values()),
    }
