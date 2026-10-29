"""回复文本生成。格式固定，同样输入得到同样文本。"""
from . import config
from .precheck import CONCLUSION_OK, CONCLUSION_FAIL, CONCLUSION_REVIEW

DIGITS = "零一二三四五六七八九"


def cn_count(n):
    if n < 10:
        return DIGITS[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + DIGITS[n - 10]
    if n < 100:
        return DIGITS[n // 10] + "十" + (DIGITS[n % 10] if n % 10 else "")
    return str(n)


def _g(x):
    return "%g" % x


def request_line(name, req, primary, primary_explicit):
    parts = ["企业=%s" % name, "金额=%s 万元" % _g(req["amount"]), "期限=%s 个月" % req["term"], "用途=%s" % req["purpose"]]
    if req.get("guarantee"):
        parts.append("担保方式=%s" % req["guarantee"])
    parts.append("主条件=%s%s" % (primary, "" if primary_explicit else "（未说明，默认）"))
    return "已确认需求：" + "、".join(parts) + "。"


def card_line(c):
    s = "%d. %s，额度 %s，年化利率 %s（%s），担保方式 %s，还款方式 %s" % (
        c["rank"], c["product_name"], c["amount_range"].replace("-", "–"), c["rate_range"], c["note"], c["guarantee"], c["repay_method"])
    if c["subsidy"]:
        s += "，" + c["subsidy_note"]
    return s


def result_line(r):
    s = "- %s：%s" % (r["product_name"], r["conclusion"])
    tail = list(r["reasons"]) + list(r["unknowns"])
    if tail:
        s += "：" + "、".join(tail)
    return s


def _fv(v):
    return "未获取" if v is None else str(v)


def _yn(v):
    return "未获取" if v is None else ("是" if v == 1 else "否")


def facts_lines(facts, fh_count):
    g = lambda t, c: facts.get(t, {}).get(c)
    e = "工商：成立日期 %s（enterprise.founded_date），经营状态 %s（enterprise.business_status），区域 %s（enterprise.region），行业 %s（enterprise.industry），科技资质 高新=%s／专精特新=%s／有效发明专利=%s" % (
        _fv(g("enterprise", "founded_date")), _fv(g("enterprise", "business_status")), _fv(g("enterprise", "region")), _fv(g("enterprise", "industry")),
        _yn(g("enterprise", "is_high_tech")), _yn(g("enterprise", "is_specialized")), _fv(g("enterprise", "invention_patent_count")))
    t = "税务：纳税等级 %s、纳税人资格 %s、欠缴 %s、近12个月纳税额 %s 万元（tax.tax_credit_rating／taxpayer_type／tax_arrears／tax_paid_12m_wan）" % (
        _fv(g("tax", "tax_credit_rating")), _fv(g("tax", "taxpayer_type")), _yn(g("tax", "tax_arrears")), _fv(g("tax", "tax_paid_12m_wan")))
    s = "社保：参保人数 %s 人、欠缴 %s（social_security.insured_count／social_security_arrears）" % (
        _fv(g("social_security", "insured_count")), _yn(g("social_security", "social_security_arrears")))
    j = "司法：严重失信 %s、被执行 %s（judicial.is_dishonest／is_enforced）" % (_yn(g("judicial", "is_dishonest")), _yn(g("judicial", "is_enforced")))
    if fh_count is None:
        h = "历史融资：未获取"
    elif fh_count == 0:
        h = "历史融资：库内无历史融资记录，按无逾期未结清处理（financing_history）"
    else:
        h = "历史融资：%d 笔，逾期未结清 %s（financing_history.overdue_unsettled）" % (fh_count, _yn(g("financing_history", "overdue_unsettled")))
    return [e, t, s, j, h]


def detail_lines(results):
    out = []
    for r in results:
        if r["conclusion"] == CONCLUSION_OK:
            continue
        items = [d["text"] for d in r["details"] if d["status"] == "fail"]
        unk = [d["text"] for d in r["details"] if d["status"] == "unknown"]
        line = "· %s（%s）：" % (r["product_name"], r["conclusion"])
        line += "；".join(items) if items else ""
        if unk:
            line += ("；" if items else "") + "未获取：" + "；".join(unk)
        if r["unknowns"] and any(u.startswith("条款未识别") for u in r["unknowns"]):
            line += "；" + "、".join(u for u in r["unknowns"] if u.startswith("条款未识别"))
        out.append(line)
    return out


def precheck_reply(name, req, primary, primary_explicit, results, ranking, facts, fh_count, solution_id, extra_notes=()):
    lines = [request_line(name, req, primary, primary_explicit)]
    n = ranking["eligible_count"]
    if n == 0:
        lines.append("按%s。暂无合适产品：无准入符合产品，推荐名单为空。" % primary)
    else:
        lines.append("按%s。准入符合 %d 个，推荐名单：" % (primary, n))
        lines.extend(card_line(c) for c in ranking["cards"])
        if n > len(ranking["cards"]):
            lines.append("（准入符合的其余 %d 个产品仍在预审明细里保持准入符合，名单最多列 %d 个。）" % (n - len(ranking["cards"]), config.RULES["max_recommend"]))
    lines.append("%s份产品的预审明细：" % cn_count(len(results)))
    lines.extend(result_line(r) for r in results)
    review = [r for r in results if r["conclusion"] == CONCLUSION_REVIEW]
    if review:
        lines.append("需人工复核的产品没有硬性不符，但有字段未获取（%s），不进入推荐名单；我没有把空字段写成正常、无欠缴或无逾期。" % "、".join(sorted({u for r in review for u in r["unknowns"]})))
    dl = detail_lines(results)
    if dl:
        lines.append("命中规则明细：")
        lines.extend(dl)
    lines.append("依据（企业画像，来自融资数据.sqlite）：")
    lines.extend("· " + x for x in facts_lines(facts, fh_count))
    lines.append("口径：额度和利率是产品库公示区间，不是授信审批结果，也不是单一执行利率；产品数字取自说明书第三条至第六条；%s" % "不参与预审的列（员工人数、核准日期、注册地址、集团、营业收入、纳税信用描述等）未使用。")
    lines.extend(extra_notes)
    if solution_id:
        lines.append("已生成融资对接单 %s，状态：待确认。回复「确认」保存，或回复「放弃」。确认只保存预审结果，不提交贷款申请。" % solution_id)
    return "\n".join(lines)


def compact_reply(name, req, primary, results, ranking, solution_id, status):
    names = "、".join(c["product_name"] for c in ranking["cards"]) or "无"
    head = "%s｜%s 万元｜%s 个月｜%s｜按%s：" % (name, _g(req["amount"]), req["term"], req["purpose"], primary)
    if ranking["eligible_count"]:
        head += "准入符合 %d 个，推荐名单：%s。" % (ranking["eligible_count"], names)
    else:
        head += "暂无合适产品：无准入符合产品，推荐名单为空。"
    ok = [r for r in results if r["conclusion"] == CONCLUSION_OK]
    bad = [r for r in results if r["conclusion"] != CONCLUSION_OK]
    parts = []
    if ok:
        parts.append("符合：" + "、".join(r["product_name"] for r in ok))
    if bad:
        parts.append("不符或复核：" + "；".join("%s（%s）" % (r["product_name"], "、".join(r["reasons"] + r["unknowns"])) for r in bad))
    s = head + "\n  " + "\n  ".join(parts)
    s += "\n  方案 %s，状态：%s。" % (solution_id, status)
    return s


def gov_text(st, pending=None, abandoned=None):
    if st["empty"]:
        return "政府视图只统计已确认方案。暂无已确认方案，不显示匹配率。"
    lines = ["政府视图只统计已确认方案。现在已确认 %d 笔。" % st["confirmed_count"]]
    extra = []
    if pending:
        extra.append("待确认：" + "、".join(pending))
    if abandoned:
        extra.append("已放弃：" + "、".join(abandoned))
    if extra:
        lines.append("；".join(extra) + "。待确认和已放弃不进入视图。")
    lines.append("区域融资热度：" + "，".join("%s %d" % (x["region"], x["count"]) for x in st["region_heat"]) + "。")
    lines.append("行业分布：" + "，".join("%s %d" % (x["industry"], x["count"]) for x in st["industry_distribution"]) + "。")
    pr = st["product_ranking"]
    lines.append("产品排行：" + ("，".join("%s %d" % (x["product_name"], x["count"]) for x in pr) if pr else "暂无准入符合产品") + "。只计已确认方案里准入符合的产品，不符和需人工复核不计。")
    mr = st["match_rate"]
    lines.append("供需匹配率：%s。分子是至少有 1 个准入符合产品的已确认方案，分母是全部已确认方案。" % mr["text"])
    return "\n".join(lines)


def report_text(rp, st):
    if rp["empty"]:
        return "分析报告只收录已确认方案。暂无已确认方案。"
    lines = ["分析报告用的是和政府视图同一批已确认方案（共 %d 笔，供需匹配率 %s）。" % (rp["confirmed_count"], rp["match_rate"]["text"])]
    lines.append("企业：" + "；".join("%s（%s，%s；准入符合产品：%s）" % (e["enterprise_name"], e["region"], e["industry"], "、".join(e["eligible_products"]) or "无") for e in rp["enterprise_section"]) + "。")
    lines.append("产品：" + ("；".join("%s %d 次（%s）" % (p["product_name"], p["count"], "、".join(p["enterprises"])) for p in rp["product_section"]) or "暂无准入符合产品") + "。")
    lines.append("区域：" + "；".join("%s %d（%s）" % (r["region"], r["count"], "、".join(r["enterprises"])) for r in rp["region_section"]) + "。")
    lines.append("待确认和已放弃的需求不出现在报告里，报告不收录未确认需求。")
    return "\n".join(lines)
