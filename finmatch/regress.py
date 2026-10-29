"""回归评测：按 tests/eval_cases.json 逐回合跑 Agent，断言结论、产品名单、状态路径和调用链健康度。

用例是自建的行为约束（来自题目说明的规则），不含公开/非公开测评集的标准结果。
断言项（每回合 expect）：
  state / contains / not_contains / eligible（名单完全一致）/ eligible_count / eligible_includes
  flow_includes / flow_excludes（状态路径里必须出现 / 不得出现的状态）/ ctx_has（会话上下文里必须有的键）
默认还断言：调用链没有 error（trace.error 为空）。
"""
import json
import os

from . import config

CASES = os.path.join(config.ROOT, "tests", "eval_cases.json")


def load(path=None):
    with open(path or CASES, encoding="utf-8") as f:
        return json.load(f)


def _check(agent, sid, out, ex):
    errs = []
    t = out.get("trace", {})
    if t.get("error") and not ex.get("allow_error"):
        errs.append("调用链出错：%s" % t["error"])
    if "state" in ex and out.get("state") != ex["state"]:
        errs.append("state=%s，期望 %s" % (out.get("state"), ex["state"]))
    for k in ex.get("contains", []):
        if k not in out["reply"]:
            errs.append("回复缺少「%s」" % k)
    for k in ex.get("not_contains", []):
        if k in out["reply"]:
            errs.append("回复不该出现「%s」" % k)
    cards = [c["product_name"] for c in (out.get("result") or {}).get("cards", [])]
    if "eligible" in ex and cards != ex["eligible"]:
        errs.append("名单=%s，期望 %s" % (cards, ex["eligible"]))
    if "eligible_count" in ex and (out.get("result") or {}).get("eligible_count") != ex["eligible_count"]:
        errs.append("准入数=%s，期望 %s" % ((out.get("result") or {}).get("eligible_count"), ex["eligible_count"]))
    for n in ex.get("eligible_includes", []):
        if n not in [r["product_name"] for r in (out.get("result") or {}).get("results", []) if r["conclusion"] == "准入符合"]:
            errs.append("准入符合里缺少 %s" % n)
    flow = " ".join(t.get("flow", []))
    for st in ex.get("flow_includes", []):
        if st not in flow:
            errs.append("状态路径缺少 %s：%s" % (st, flow))
    for st in ex.get("flow_excludes", []):
        if st in flow:
            errs.append("状态路径不该出现 %s：%s" % (st, flow))
    for k in ex.get("ctx_has", []):
        if not agent._ctx(sid).get(k):
            errs.append("会话上下文缺少 %s" % k)
    return errs


def run(agent, cases=None, only=None):
    """返回 [{id, name, passed, failures:[{turn, say, errors}]}]。"""
    report = []
    for case in cases or load():
        if only and case["id"] not in only:
            continue
        fails = []
        for i, turn in enumerate(case["turns"], 1):
            sid = "RG-%s-%s" % (case["id"], turn.get("sid", "main"))
            for _ in range(turn.get("repeat", 1)):
                out = agent.handle(sid, turn["say"] + "甲" * turn.get("pad", 0))
            errs = _check(agent, sid, out, turn.get("expect", {}))
            if errs:
                fails.append({"turn": i, "say": turn["say"][:40], "errors": errs, "trace_id": out.get("trace_id")})
        report.append({"id": case["id"], "name": case["name"], "passed": not fails, "failures": fails})
    return report
