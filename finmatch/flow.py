"""编排层：显式状态机 + 规划 + 路由。流程由转移表控制，不靠提示词硬编；模型只提议意图与工具，走哪一步由转移表和代码裁决。

一个回合的主干：START → PARSE → ROUTE → 需求流水线 / ACT / INFO → END
需求流水线：COLLECT → LOOKUP → AUTH → CONSISTENCY → FETCH → PRECHECK → RANK → REFLECT → SAVE → EXPLAIN → END
任何一步都可以因为「缺项追问 / 未收录 / 待授权 / 材料不一致 / 失败」直接转到 END，并在 trace 里留下原因。
REFLECT 自检不通过可回到 PRECHECK 重做，最多 MAX_REFLECT 次，仍不通过则降级（把有问题的产品改判「需人工复核」）。
"""
TRANSITIONS = {
    "START": {"PARSE"},
    "PARSE": {"ROUTE", "END"},
    "ROUTE": {"COLLECT", "ACT", "INFO", "END"},
    "COLLECT": {"LOOKUP", "END"},
    "LOOKUP": {"AUTH", "END"},
    "AUTH": {"CONSISTENCY", "END"},
    "CONSISTENCY": {"FETCH", "END"},
    "FETCH": {"PRECHECK", "END"},
    "PRECHECK": {"RANK", "END"},
    "RANK": {"REFLECT", "END"},
    "REFLECT": {"PRECHECK", "SAVE", "END"},
    "SAVE": {"EXPLAIN", "END"},
    "EXPLAIN": {"END"},
    "ACT": {"END"},
    "INFO": {"END"},
    "END": {"COLLECT", "ACT", "INFO", "END"},  # 一句话里有多家企业时，每家各走一遍
}
MAX_REFLECT = 1

# 每个状态由哪个子 Agent 负责、会用到哪些工具（路由表）
ROUTES = {
    "COLLECT": ("planner", []),
    "LOOKUP": ("data_agent", ["query_enterprise"]),
    "AUTH": ("data_agent", ["query_enterprise"]),
    "CONSISTENCY": ("planner", []),
    "FETCH": ("data_agent", ["query_tax", "query_social_security", "query_judicial", "query_financing_history"]),
    "PRECHECK": ("precheck_agent", ["query_product_table"]),
    "RANK": ("rank_agent", ["rank_products"]),
    "REFLECT": ("reflect_agent", []),
    "SAVE": ("planner", ["save_solution"]),
    "EXPLAIN": ("explain_agent", ["llm_summary"]),
    "ACT": ("planner", ["save_solution"]),
    "INFO": ("planner", ["gov_view_stats", "memory_list"]),
}
PIPELINE = ["COLLECT", "LOOKUP", "AUTH", "CONSISTENCY", "FETCH", "PRECHECK", "RANK", "REFLECT", "SAVE", "EXPLAIN"]


class FlowError(RuntimeError):
    pass


def make_plan(has_demand, has_act, has_info):
    """规划：根据本轮识别出的意图，列出要执行的步骤、负责的子 Agent 和工具。执行时逐步打勾，没走到的标记为跳过及原因。"""
    steps = []
    if has_demand:
        steps += PIPELINE
    if has_act:
        steps.append("ACT")
    if has_info:
        steps.append("INFO")
    return [{"step": s, "agent": ROUTES[s][0], "tools": ROUTES[s][1], "status": "pending"} for s in steps]


class Flow:
    def __init__(self, trace):
        self.trace = trace
        self.cur = "START"
        self.reflects = 0
        trace.flow.append("START")

    def go(self, state, why=None):
        if state not in TRANSITIONS.get(self.cur, ()):
            raise FlowError("非法状态转移 %s → %s" % (self.cur, state))
        if self.cur == "REFLECT" and state == "PRECHECK":
            self.reflects += 1
            if self.reflects > MAX_REFLECT:
                raise FlowError("自检重试超过 %d 次" % MAX_REFLECT)
        self.cur = state
        self.trace.node = state
        self.trace.flow.append(state if not why else "%s(%s)" % (state, why))
        for p in self.trace.plan:
            if p["step"] == state and p["status"] == "pending":
                p["status"] = "done"
                break

    def end(self, why):
        """正常结束或提前结束（追问、未收录、待授权等），没走到的计划步骤标记为跳过。"""
        if self.cur != "END":
            self.go("END", why)
        for p in self.trace.plan:
            if p["status"] == "pending":
                p["status"] = "skipped"
                p["reason"] = why
