"""准入预审引擎。只读产品表记录和白名单列；规则顺序、文案来自 config/rules.json。"""
import re
from datetime import date

from . import config

OK, FAIL, UNK = "pass", "fail", "unknown"
CONCLUSION_OK, CONCLUSION_FAIL, CONCLUSION_REVIEW = "准入符合", "准入不符", "需人工复核"


class Facts:
    """预审可见的事实。只允许读取 rules.json 白名单里的列，其它列读取即报错。"""

    def __init__(self, data):
        # data: {table: {col: value}}；failed: 取数失败的来源表
        self.data = data
        self.failed = set()

    def get(self, table, col):
        if col not in config.WHITELIST.get(table, ()):
            raise PermissionError("列 %s.%s 不参与预审，不得读取" % (table, col))
        if table in self.failed:
            return None
        return self.data.get(table, {}).get(col)

    def field(self, table, col):
        return "%s.%s" % (table, col)


def _fmt(x):
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _in_cell(values, v):
    for item in values:
        if item == v:
            return True
    # 行业名自身可能含顿号（如「交通运输、仓储和邮政业」），按整词边界再比一次
    joined = "、".join(values)
    return re.search(r"(?:^|、)%s(?:、|$)" % re.escape(v), joined) is not None


def anniversary_reached(founded, years, base=None):
    base = base or config.BASE_DATE
    try:
        d = date.fromisoformat(founded)
    except Exception:
        return None
    y = d.year + years
    try:
        ann = d.replace(year=y)
    except ValueError:  # 2 月 29 日，非闰年按 3 月 1 日算周年
        ann = date(y, 3, 1)
    return ann <= base, ann


def _rule_results(p, f, req):
    cond = p["conditions"]
    R = []  # (rule_id, status, text, fields)

    def add(rid, status, text, fields):
        R.append((rid, status, text, fields))

    v = f.get("enterprise", "business_status")
    if "business_status" in cond:
        if v is None:
            add("business_status", UNK, "经营状态为空", ["enterprise.business_status"])
        elif v == config.RULES["active_status"]:
            add("business_status", OK, "经营状态 %s" % v, ["enterprise.business_status"])
        else:
            add("business_status", FAIL, "经营状态为「%s」，不是在营" % v, ["enterprise.business_status"])

    for rid, table, col, name in (("tax_arrears", "tax", "tax_arrears", "税务欠缴"),
                                  ("ss_arrears", "social_security", "social_security_arrears", "社保欠缴")):
        if rid in cond:
            v = f.get(table, col)
            fld = [f.field(table, col)]
            if v is None:
                add(rid, UNK, "%s字段为空" % name, fld)
            elif v == 1:
                add(rid, FAIL, "%s=是" % name, fld)
            else:
                add(rid, OK, "%s=否" % name, fld)

    if "overdue" in cond:
        v = f.get("financing_history", "overdue_unsettled")
        fld = ["financing_history.overdue_unsettled"]
        if v is None:
            add("overdue", UNK, "逾期未结清字段为空", fld)
        elif v == 1:
            add("overdue", FAIL, "存在逾期未结清的历史融资", fld)
        else:
            add("overdue", OK, "无逾期未结清", fld)

    amt = req["amount"]
    if amt < p["amount_min"]:
        add("amount_low", FAIL, "需求 %s 万元 < 产品下限 %s 万元（第五条）" % (_fmt(amt), _fmt(p["amount_min"])), ["第五条 额度"])
    else:
        add("amount_low", OK, "需求 %s 万元 ≥ 下限 %s 万元" % (_fmt(amt), _fmt(p["amount_min"])), ["第五条 额度"])
    if amt > p["amount_max"]:
        add("amount_high", FAIL, "需求 %s 万元 > 产品上限 %s 万元（第五条）" % (_fmt(amt), _fmt(p["amount_max"])), ["第五条 额度"])
    else:
        add("amount_high", OK, "需求 %s 万元 ≤ 上限 %s 万元" % (_fmt(amt), _fmt(p["amount_max"])), ["第五条 额度"])

    t = req["term"]
    rng = "%s–%s 个月" % (p["term_min"], p["term_max"])
    if p["term_min"] <= t <= p["term_max"]:
        add("term", OK, "期限 %s 个月在 %s 内" % (t, rng), ["第三条 期限"])
    else:
        add("term", FAIL, "期限 %s 个月不在 %s（第三条）" % (t, rng), ["第三条 期限"])

    if req["purpose"] in p["purposes"]:
        add("purpose", OK, "用途「%s」在适用用途内" % req["purpose"], ["第三条 适用用途"])
    else:
        add("purpose", FAIL, "用途「%s」不在适用用途「%s」（第三条，用词须完全一致）" % (req["purpose"], "、".join(p["purposes"])), ["第三条 适用用途"])

    v = f.get("enterprise", "region")
    if not p["regions"]:
        add("region", OK, "区域写明不限", ["第四条 适用区域"])
    elif v is None:
        add("region", UNK, "区域字段为空", ["enterprise.region"])
    elif _in_cell(p["regions"], v):
        add("region", OK, "区域「%s」符合" % v, ["enterprise.region"])
    else:
        add("region", FAIL, "工商区域「%s」不在适用区域「%s」（第四条）" % (v, "、".join(p["regions"])), ["enterprise.region"])

    v = f.get("enterprise", "industry")
    if not p["industries"]:
        add("industry", OK, "行业写明不限", ["第四条 适用行业"])
    elif v is None:
        add("industry", UNK, "行业字段为空", ["enterprise.industry"])
    elif _in_cell(p["industries"], v):
        add("industry", OK, "行业「%s」符合" % v, ["enterprise.industry"])
    else:
        add("industry", FAIL, "工商行业「%s」不在适用行业「%s」（第四条）" % (v, "、".join(p["industries"])), ["enterprise.industry"])

    if "rating" in cond:
        need = cond["rating"]["value"]
        v = f.get("tax", "tax_credit_rating")
        fld = ["tax.tax_credit_rating"]
        if v is None or v not in config.RATING_RANK:
            add("rating", UNK, "纳税等级为空或无法识别", fld)
        elif config.RATING_RANK[v] >= config.RATING_RANK[need]:
            add("rating", OK, "纳税等级 %s，要求不低于 %s" % (v, need), fld)
        else:
            add("rating", FAIL, "纳税等级 %s 低于要求 %s（第六条）" % (v, need), fld)

    if "insured" in cond:
        need = cond["insured"]["value"]
        v = f.get("social_security", "insured_count")
        fld = ["social_security.insured_count"]
        if v is None:
            add("insured", UNK, "参保人数为空", fld)
        elif v >= need:
            add("insured", OK, "参保 %s 人，要求不少于 %s 人" % (v, need), fld)
        else:
            add("insured", FAIL, "参保 %s 人 < 要求 %s 人（第六条）" % (v, need), fld)

    if "founded" in cond:
        need = cond["founded"]["value"]
        v = f.get("enterprise", "founded_date")
        fld = ["enterprise.founded_date"]
        res = anniversary_reached(v, need) if v else None
        if res is None:
            add("founded", UNK, "成立日期为空或格式无法识别", fld)
        elif res[0]:
            add("founded", OK, "成立日期 %s，满 %s 年周年日 %s ≤ 基准日 %s" % (v, need, res[1], config.BASE_DATE), fld)
        else:
            add("founded", FAIL, "成立日期 %s，满 %s 年的周年日 %s 晚于基准日 %s（第六条）" % (v, need, res[1], config.BASE_DATE), fld)

    if "tax_paid" in cond:
        need = cond["tax_paid"]["value"]
        v = f.get("tax", "tax_paid_12m_wan")
        fld = ["tax.tax_paid_12m_wan"]
        if v is None:
            add("tax_paid", UNK, "近12个月纳税额为空", fld)
        elif v >= need:
            add("tax_paid", OK, "近12个月纳税额 %s 万元 ≥ %s 万元" % (_fmt(v), _fmt(need)), fld)
        else:
            add("tax_paid", FAIL, "近12个月纳税额 %s 万元 < 要求 %s 万元（第六条）" % (_fmt(v), _fmt(need)), fld)

    if "dishonest" in cond:
        a, b = f.get("judicial", "is_dishonest"), f.get("judicial", "is_enforced")
        fld = ["judicial.is_dishonest", "judicial.is_enforced"]
        if a == 1 or b == 1:
            add("dishonest", FAIL, "严重失信=%s，被执行=%s（任一为是即失信限制）" % ("是" if a == 1 else "否" if a == 0 else "空", "是" if b == 1 else "否" if b == 0 else "空"), fld)
        elif a is None or b is None:
            add("dishonest", UNK, "严重失信或被执行字段为空", fld)
        else:
            add("dishonest", OK, "严重失信=否，被执行=否", fld)

    if "tech" in cond:
        ht, sp, pt = f.get("enterprise", "is_high_tech"), f.get("enterprise", "is_specialized"), f.get("enterprise", "invention_patent_count")
        fld = ["enterprise.is_high_tech", "enterprise.is_specialized", "enterprise.invention_patent_count"]
        if ht == 1 or sp == 1 or (pt is not None and pt >= 1):
            add("tech", OK, "科技资质满足（高新=%s，专精特新=%s，发明专利=%s）" % (ht, sp, pt), fld)
        elif ht is None or sp is None or pt is None:
            add("tech", UNK, "科技资质字段有空值且无任一项满足", fld)
        else:
            add("tech", FAIL, "高新=%s、专精特新=%s、有效发明专利=%s，均不满足（第六条）" % (ht, sp, pt), fld)

    if "taxpayer" in cond:
        need = cond["taxpayer"]["value"]
        v = f.get("tax", "taxpayer_type")
        fld = ["tax.taxpayer_type"]
        if v is None:
            add("taxpayer", UNK, "纳税人资格为空", fld)
        elif v == need:
            add("taxpayer", OK, "纳税人资格 %s" % v, fld)
        else:
            add("taxpayer", FAIL, "纳税人资格为「%s」，要求「%s」（第六条）" % (v, need), fld)

    if req.get("guarantee"):
        if req["guarantee"] in p["guarantees"]:
            add("guarantee", OK, "担保方式 %s 一致" % req["guarantee"], ["第五条 担保方式"])
        else:
            add("guarantee", FAIL, "客户指定担保方式「%s」，产品为「%s」（第五条）" % (req["guarantee"], "、".join(p["guarantees"])), ["第五条 担保方式"])
    return R


def precheck_product(p, facts, req):
    results = _rule_results(p, facts, req)
    order = {rid: i for i, rid in enumerate(config.RULE_ORDER)}
    results.sort(key=lambda r: order[r[0]])
    reasons = [config.RULE_LABEL[r[0]] for r in results if r[1] == FAIL]
    unknown_tags, unknown_fields = [], []
    for rid, st, text, fld in results:
        if st == UNK:
            tag = config.RULE_SOURCE[rid] + "未获取"
            if tag not in unknown_tags:
                unknown_tags.append(tag)
            unknown_fields.extend(fld)
    for e in p["extra_conditions"]:
        unknown_tags.append("条款未识别：%s" % e["name"])
    if reasons:
        conclusion = CONCLUSION_FAIL
    elif unknown_tags:
        conclusion = CONCLUSION_REVIEW
    else:
        conclusion = CONCLUSION_OK
    return {
        "product_code": p["product_code"],
        "product_name": p["product_name"],
        "conclusion": conclusion,
        "reasons": reasons,
        "unknowns": unknown_tags,
        "unknown_fields": sorted(set(unknown_fields)),
        "details": [{"rule": r[0], "label": config.RULE_LABEL[r[0]], "status": r[1], "text": r[2], "fields": r[3]} for r in results],
    }


def precheck_all(products, facts, req):
    return [precheck_product(p, facts, req) for p in products]
