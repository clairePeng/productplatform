"""子 Agent：按职责拆分，每个只做一件事，输入输出都是结构化数据，由编排层（agent.py + flow.py）串联。

DataAgent     取数：五类政务数据，超时/重试/降级由 call_tool 负责
PrecheckAgent 预审：按产品表逐产品判定，汇总命中规则
RankAgent     排序：只排「准入符合」
ReflectAgent  自检：核对预审与排序的不变量，不通过则给出问题清单；编排层据此重做或降级
ExplainAgent  解读：调模型写简短解读（可选，失败即省略）
"""
from . import ranking
from .precheck import precheck_all, CONCLUSION_OK, CONCLUSION_REVIEW
from .tools import call_tool


class DataAgent:
    def __init__(self, spec, to_vals):
        self.spec = spec  # [(工具名, 函数, 摘要函数, 表名)]
        self.to_vals = to_vals

    def fetch(self, trace, enterprise_id, facts, data, only=None):
        """取数写入 data，失败的来源记入 facts.failed。返回历史融资条数（没取到为 None）。"""
        fh_count = None
        with trace.span("FETCH", "data_agent"):
            for tname, fn, summ, table in self.spec:
                if only and table not in only:
                    continue
                ok, res = call_tool(trace, tname, fn, {"enterprise_id": enterprise_id}, summ)
                if ok:
                    data[table] = self.to_vals(res)
                    facts.failed.discard(table)
                    if table == "financing_history":
                        fh_count = res["count"]
                else:
                    facts.failed.add(table)
        return fh_count


class PrecheckAgent:
    def run(self, trace, prods, facts, req):
        with trace.span("PRECHECK", "precheck_agent"):
            results = precheck_all(prods, facts, req)
            hit = {}
            for r in results:
                for d in r["details"]:
                    if d["status"] == "fail":
                        hit[d["label"]] = hit.get(d["label"], 0) + 1
        return results, hit


class RankAgent:
    def run(self, trace, results, prods, primary, industry):
        with trace.span("RANK", "rank_agent"):
            return ranking.rank(results, prods, primary, industry)


class ReflectAgent:
    """自检不改结果，只报告问题。「准入符合」的不变量：无失败规则、无未获取项、名单只含准入符合产品。"""

    def check(self, trace, results, rk, req, product_count):
        issues = []
        with trace.span("REFLECT", "reflect_agent"):
            if len(results) != product_count:
                issues.append("预审结果数（%d）与产品数（%d）不一致" % (len(results), product_count))
            ok_names = {r["product_name"] for r in results if r["conclusion"] == CONCLUSION_OK}
            if rk["eligible_count"] != len(ok_names):
                issues.append("排序准入数（%d）与预审准入数（%d）不一致" % (rk["eligible_count"], len(ok_names)))
            for c in rk["cards"]:
                if c["product_name"] not in ok_names:
                    issues.append("推荐名单含非准入符合产品：%s" % c["product_name"])
            for r in results:
                if r["conclusion"] != CONCLUSION_OK:
                    continue
                if any(d["status"] in ("fail", "unknown") for d in r["details"]) or r.get("unknowns"):
                    issues.append("%s 判为准入符合，但存在未通过或未获取的规则" % r["product_name"])
            if not (isinstance(req["term"], int) and req["term"] > 0 and req["amount"] > 0):
                issues.append("需求金额或期限不合法")
        return issues

    def degrade(self, results, issues):
        """重做后仍不通过：把名单里有问题的产品改判需人工复核，不把有疑问的结果当作准入符合交给用户。"""
        for r in results:
            if r["conclusion"] == CONCLUSION_OK and any(r["product_name"] in i for i in issues):
                r["conclusion"] = CONCLUSION_REVIEW
                r["unknowns"] = list(r.get("unknowns", [])) + ["自检未通过"]
        return results
