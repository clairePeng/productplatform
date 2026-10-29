"""上下文管理 / Agent 编排 / 可观测 的专项测试。"""
import json

import pytest

from conftest import ROOT  # noqa: F401
from finmatch import agent as A, context, flow as FL, llm, obs, regress
from finmatch.tools import Trace, call_tool

Q = "华峻精密制造有限公司要流动资金 100 万，期限 12 个月"


# ---------- 回归评测集 ----------
def test_regression_suite(fresh):
    bad = [r for r in regress.run(fresh) if not r["passed"]]
    assert not bad, json.dumps(bad, ensure_ascii=False, indent=1)


def test_regression_can_fail(fresh):
    """断言不是摆设：期望写错必须报失败。"""
    cases = [{"id": "X", "name": "x", "turns": [{"say": Q, "expect": {"eligible": ["不存在的产品"]}}]}]
    assert not regress.run(fresh, cases)[0]["passed"]


# ---------- 上下文管理 ----------
def test_clip_and_budget():
    assert "已截断，原长" in context.clip("x" * 1000, 100)
    assert context.clip_list(list(range(50)), 5)[-1].startswith("…另 45 项")
    msgs = [{"role": "user", "content": "很长的一句话" * 100} for _ in range(30)]
    out = context.llm_context({"summary": "摘要" * 500}, msgs, budget=300)
    assert out == {} or context.est_tokens(out) <= 300  # 超预算宁可不带，也不超
    assert context.llm_context({}, msgs[:2])["recent"]


def test_compaction_keeps_structured_memory(fresh):
    sid = "C1"
    fresh.handle(sid, Q)
    for _ in range(20):
        fresh.handle(sid, "你好")
    ctx = fresh._ctx(sid)
    assert ctx["summary"] and "华峻精密制造有限公司" in ctx["summary"] and ctx["slots"]["term"] == 12
    assert len(fresh.history(sid, 10)) == 10 and len(fresh.history(sid)) > 40  # 页面可回看，模型只拿裁剪后的


def test_sessions_isolated(fresh):
    fresh.handle("ISO-A", Q)
    assert fresh._ctx("ISO-B").get("company") is None
    o = fresh.handle("ISO-B", "期限 12 个月")
    assert o["state"] == "ask" and "企业名称" in o["reply"]
    assert not any("华峻" in m["content"] for m in fresh.history("ISO-B"))


def test_tool_result_truncated_in_trace():
    t = Trace()
    call_tool(t, "big", lambda x: x, {"x": "数据" * 5000}, lambda r: "返回" * 5000)
    e = t.tools[0]
    assert len(e["result_summary"]) < 400 and len(json.dumps(e["args"], ensure_ascii=False)) < 400


def test_llm_overflow_retries_without_context(fresh, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "t")
    calls = []

    def fake(payload):
        user = payload["messages"][1]["content"]
        calls.append("context" in user)
        if "context" in user:
            raise RuntimeError("HTTP 400 maximum context length exceeded")
        return {"choices": [{"message": {"content": json.dumps({"term_months": 12})}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr(llm, "_transport", fake)
    got = llm.extract("期限 12 个月", None, [], set(), context={"summary": "x", "recent": []})
    assert calls == [True, False] and got == {"term": 12}


def test_oversized_llm_request_not_sent(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "t")
    monkeypatch.setattr(llm, "_transport", lambda p: pytest.fail("超预算请求不应发出"))
    assert llm.chat("s", "字" * 20000) is None


# ---------- 编排：状态机 / 自检 / 降级 ----------
def test_illegal_transition_rejected():
    f = FL.Flow(Trace())
    f.go("PARSE")
    with pytest.raises(FL.FlowError):
        f.go("PRECHECK")


def test_trace_has_plan_flow_and_skips(fresh):
    t = fresh.handle("F1", "诚金金属制品有限公司要流动资金 100 万，12 个月")["trace"]
    assert t["flow"][-1].startswith("END(unauthorized)") and "FETCH" not in " ".join(t["flow"])
    skipped = {p["step"] for p in t["plan"] if p["status"] == "skipped"}
    assert {"FETCH", "PRECHECK"} <= skipped and all(p["reason"] == "unauthorized" for p in t["plan"] if p["status"] == "skipped")
    t = fresh.handle("F2", Q)["trace"]
    assert [p["status"] for p in t["plan"]] == ["done"] * 9 + ["skipped"]  # 没配模型时 EXPLAIN 跳过
    assert {s["agent"] for s in t["spans"]} >= {"data_agent", "precheck_agent", "rank_agent", "reflect_agent"}


def test_reflect_retry_then_ok(fresh, monkeypatch):
    seen = []
    orig = A.REFLECT.check

    def once(trace, results, rk, req, n):
        seen.append(1)
        return ["模拟自检问题"] if len(seen) == 1 else orig(trace, results, rk, req, n)
    monkeypatch.setattr(A.REFLECT, "check", once)
    o = fresh.handle("F3", Q)
    assert o["state"] == "done" and "PRECHECK(重做)" in o["trace"]["flow"] and len(seen) == 2


def test_reflect_degrade_never_returns_doubtful_eligible(fresh, monkeypatch):
    name = "税信e贷"
    monkeypatch.setattr(A.REFLECT, "check", lambda tr, r, rk, req, n: ["%s 判为准入符合，但存在未获取的规则" % name])
    o = fresh.handle("F4", Q)
    res = {r["product_name"]: r["conclusion"] for r in o["result"]["results"]}
    assert res[name] == "需人工复核" and name not in [c["product_name"] for c in o["result"]["cards"]]
    assert o["trace"]["flow"].count("PRECHECK(重做)") == 1
    assert obs.get_trace(o["trace_id"])["status"] == "degraded"


def test_reflect_catches_real_inconsistency(fresh, monkeypatch):
    """自检不是摆设：预审把有失败规则的产品判成准入符合时，必须报问题。"""
    from finmatch.precheck import precheck_all
    orig = A.PRECHECK.run

    def bad(trace, prods, facts, req):
        results, hit = orig(trace, prods, facts, req)
        results[0]["conclusion"] = "准入符合"  # 税信e贷之外也可能本就符合；强行改写首个产品
        for d in results[0]["details"][:1]:
            d["status"] = "fail"
        return results, hit
    monkeypatch.setattr(A.PRECHECK, "run", bad)
    o = fresh.handle("F5", Q)
    assert any("自检" in r for r in o["trace"]["rules"])


def test_error_localized_to_node_and_not_crash(fresh, monkeypatch):
    def boom(*a, **k):
        raise ValueError("模拟故障")
    from finmatch import subagents
    monkeypatch.setattr(subagents, "precheck_all", boom)
    o = fresh.handle("E1", Q)
    t = o["trace"]
    assert o["state"] == "error" and t["error"]["node"] == "PRECHECK" and t["error"]["agent"] == "precheck_agent"
    assert "PRECHECK" in o["reply"] and o["trace_id"] in o["reply"]
    assert obs.get_trace(o["trace_id"])["error"]["type"] == "ValueError"
    assert fresh.handle("E1", "你好")["state"] != "error"  # 之后的回合不受影响


# ---------- 可观测 ----------
def test_usage_and_cost_recorded(fresh, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "t")

    def fake(payload):
        if "抽取器" in payload["messages"][0]["content"]:
            return {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        return {"choices": [{"message": {"content": "有 3 个产品准入符合，请选择。"}}], "usage": {"prompt_tokens": 200, "completion_tokens": 20}}
    monkeypatch.setattr(llm, "_transport", fake)
    o = fresh.handle("U1", "华峻精密制造有限公司要100万")
    o = fresh.handle("U1", "流动资金 12 个月")
    u = o["trace"]["usage"]
    assert u["calls"] >= 1 and u["total_tokens"] > 0 and u["cost_cny"] > 0
    assert all(c["model"] == llm.DISPLAY and c["usage_source"] == "api" for c in o["trace"]["llm"])
    assert o["trace"]["total_ms"] >= 0 and all("ms" in s for s in o["trace"]["spans"])


def test_llm_failure_recorded_not_fatal(fresh, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "t")
    monkeypatch.setattr(llm, "_transport", lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    o = fresh.handle("U2", "华峻精密制造有限公司要100万")
    assert o["state"] == "ask" and any(not c["ok"] and "down" in c["error"] for c in o["trace"]["llm"])


def test_trace_persisted_and_api(fresh):
    from fastapi.testclient import TestClient
    from finmatch.server import app
    o = fresh.handle("API1", Q)
    c = TestClient(app)
    lst = c.get("/api/traces", params={"session_id": "API1"}).json()["traces"]
    assert lst and lst[0]["trace_id"] == o["trace_id"] and lst[0]["status"] == "ok"
    d = c.get("/api/traces/" + o["trace_id"]).json()
    assert d["flow"] == o["trace"]["flow"] and d["session_id"] == "API1"
    assert c.get("/api/traces/nope").status_code == 404
    m = c.get("/api/metrics").json()
    assert m["turns"] >= 1 and "node_avg_ms" in m and "p95_ms" in m
    assert c.get("/api/metrics", headers={"X-Role": "gov"}).status_code == 403


# ---------- 模型提议、代码裁决 ----------
def _fake_route(monkeypatch, proposal):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "t")

    def fake(payload):
        sys_ = payload["messages"][0]["content"]
        return json.dumps(proposal) if "意图识别与工具规划器" in sys_ else "{}"
    monkeypatch.setattr(llm, "_transport", fake)


def _tool(o, name):
    return [t for t in o["trace"]["tools"] if t["name"] == name]


def test_route_read_only_intent_adopted(fresh, monkeypatch):
    _fake_route(monkeypatch, {"intents": ["gov"], "tools": ["gov_view_stats", "query_tax"], "reason": "想看统计"})
    o = fresh.handle("R1", "帮我瞅瞅整体情况怎么样")
    assert "政府视图" in o["reply"] or "已确认" in o["reply"]
    s = _tool(o, "llm_route")[0]["result_summary"]
    assert "gov" in s and "query_tax" in s  # query_tax 不在 gov 的允许工具内，被丢弃


def test_route_confirm_not_executed_without_rule_match(fresh, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    fresh.handle("R2", Q)
    _fake_route(monkeypatch, {"intents": ["confirm"], "tools": ["save_solution"], "reason": "好的"})
    o = fresh.handle("R2", "嗯就这样吧")
    assert "请直接回复" in o["reply"]
    assert fresh.solution_view("R2")["status"] == "待确认"


def test_route_invalid_proposal_falls_back(fresh, monkeypatch):
    _fake_route(monkeypatch, {"intents": ["delete_everything"], "tools": ["rm"]})
    o = fresh.handle("R3", Q)
    assert o["state"] != "error"
    assert "无有效提议" in json.dumps(o["trace"], ensure_ascii=False)
