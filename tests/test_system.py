from datetime import datetime
import json
import os

import pytest

from conftest import ROOT, names, concl
from finmatch import solutions, products as P
from finmatch.extractor import extract_product, ExtractionError


def ask(agent, sid, text):
    return agent.handle(sid, text)


# ---------- 产品抽取 ----------
def test_extract_uses_clauses_not_intro(fresh):
    p = P.get_product("PRD_01")
    assert (p["amount_min"], p["amount_max"]) == (50, 300)  # 导语里的千万级宣传数字不入库
    assert (p["rate_min"], p["rate_max"]) == (3.5, 4.2)
    assert p["conditions"]["tax_paid"]["value"] == 200
    assert "第五条" in p["source_map"]["额度区间"]["clause"]
    assert len(P.list_products()) == 11


def test_reupload_overwrites_and_bad_pdf_rejected(fresh):
    f = os.path.join(ROOT, "数据", "产品", "税信e贷.pdf")
    data = open(f, "rb").read()
    p, over = P.ingest_pdf(data, "x.pdf")
    assert over and p["upload_count"] == 2 and len(P.list_products()) == 11
    for bad in (b"", b"not a pdf", b"%PDF-1.4 broken"):
        with pytest.raises(ExtractionError):
            P.ingest_pdf(bad, "bad.pdf")
    assert len(P.list_products()) == 11


# ---------- 预审规则 ----------
def test_public_style_cases(fresh):
    o = ask(fresh, "T1", "华峻精密制造有限公司定了，流动资金 100 万，期限 12 个月。帮我配产品。")
    assert names(o) == ["税信e贷", "日常经营贷", "快e周转"]
    o = ask(fresh, "T1", "这单别按利率排了，改成额度优先。")
    assert names(o) == ["快e周转", "税信e贷", "日常经营贷"]
    assert len(solutions.list_solutions(session_id="T1")) == 1  # 同一单更新，不新建


def test_more_than_three_eligible_keeps_conclusion(fresh):
    o = ask(fresh, "T2", "峻成金属制品有限公司要流动资金 100 万，12 个月")
    r = concl(o)
    assert o["result"]["eligible_count"] == 4 and len(o["result"]["cards"]) == 3
    assert r["税金e贷"] == "准入符合"
    assert "其余 1 个产品仍在预审明细里保持准入符合" in o["reply"]


def test_unauthorized_no_data_calls(fresh):
    o = ask(fresh, "T3", "诚金金属制品有限公司要流动资金 100 万，12 个月")
    assert "待授权" in o["reply"]
    tools = [t["name"] for t in o["trace"]["tools"]]
    assert tools == ["query_enterprise"]
    with pytest.raises(Exception):
        from finmatch import govdata
        govdata.query_tax("ENT_017")


def test_unauthorized_then_grant_resumes(fresh):
    o = ask(fresh, "U1", "诚金金属制品有限公司要流动资金 100 万，12 个月")
    assert o["state"] == "unauthorized" and "已授权" in o["reply"]
    r = ask(fresh, "U1", "已授权")
    assert r["state"] == "done" and "已记录" in r["reply"] and r["result"]["request"]["amount"] == 100
    assert "query_tax" in [t["name"] for t in r["trace"]["tools"]]


def test_unauthorized_deny_stays_blocked(fresh):
    ask(fresh, "U2", "诚金金属制品有限公司要流动资金 100 万，12 个月")
    r = ask(fresh, "U2", "不授权")
    assert r["state"] == "unauthorized" and "result" not in r
    assert "query_tax" not in [t["name"] for t in r["trace"]["tools"]]
    r = ask(fresh, "U2", "已授权")  # 已拒绝后不再有待授权事项
    assert r["state"] != "done"


def test_page_authorize_and_conversation_list(fresh):
    sid = fresh.new_session()
    today = datetime.now().strftime("%Y-%m-%d")
    assert fresh.conversations()[0]["title"] == "新会话 · " + today
    o = ask(fresh, sid, "诚金金属制品有限公司要流动资金 100 万，12 个月")
    assert o["pending_auth"]["name"] == "诚金金属制品有限公司"
    c = [x for x in fresh.conversations() if x["session_id"] == sid][0]
    assert c["title"] == "诚金金属制品有限公司 · " + today and c["pending_auth"] and c["turns"] == 1
    r = fresh.authorize(sid, "grant")
    assert r["state"] == "done" and r["result"]["request"]["term"] == 12
    assert fresh.conversation(sid)["pending_auth"] is None
    sid2 = fresh.new_session()
    ask(fresh, sid2, "诚金金属制品有限公司要流动资金 100 万，12 个月")  # 已授权，直接预审
    assert fresh.conversation(sid2)["pending_auth"] is None


def test_not_found_is_exact_match(fresh):
    for name in ("华峻精密有限公司", "云岚信息科技集团有限公司", "澄川市星澜科技有限公司"):
        o = ask(fresh, "T4" + str(abs(hash(name)) % 1000), "%s要流动资金 50 万，6 个月" % name)
        assert o["state"] == "not_found", name
    o = ask(fresh, "T4x", "云岚信息科技股份有限公司要流动资金 50 万，6 个月")
    assert o["state"] == "done"
    assert o["result"]["enterprise"]["enterprise_id"] == "ENT_012"


def test_term_unit_and_missing_slots(fresh):
    o = ask(fresh, "T5", "华峻精密制造有限公司要流动资金 100 万，期限 360 天")
    assert o["state"] == "ask" and "个月" in o["reply"] and not o["trace"]["tools"]
    o = ask(fresh, "T5", "期限 1 年")
    assert o["state"] == "ask"
    o = ask(fresh, "T5", "期限 12 个月")
    assert o["state"] == "done"
    o = ask(fresh, "T5b", "华峻精密制造有限公司要贷款")
    assert o["state"] == "ask" and "融资金额" in o["reply"] and "用途" in o["reply"]


def test_purpose_exact_wording(fresh):
    o = ask(fresh, "T6", "华峻精密制造有限公司要周转资金 100 万，12 个月")
    r = concl(o)
    assert r["制造业周转贷"] == "准入符合" and r["税信e贷"] == "准入不符"
    o = ask(fresh, "T6b", "华峻精密制造有限公司要采购原材料 100 万，12 个月")
    assert o["result"]["eligible_count"] == 0 and "用途不符" in o["reply"]


def test_guarantee_mismatch(fresh):
    o = ask(fresh, "T7", "华峻精密制造有限公司要流动资金 100 万，12 个月，担保方式抵押")
    assert all("担保方式不符" in r["reasons"] for r in o["result"]["results"] if r["product_name"] != "设备抵押贷")
    o = ask(fresh, "T7b", "华峻精密制造有限公司要购置设备 100 万，24 个月，抵押")
    assert names(o) == ["设备抵押贷"]


def test_edge_enterprises(fresh):
    o = ask(fresh, "E1", "齐力电机制造有限公司要流动资金 100 万，12 个月")
    assert all("社保欠缴" in r["reasons"] for r in o["result"]["results"])
    o = ask(fresh, "E2", "通达货运有限公司要流动资金 100 万，12 个月")
    assert all("失信限制" in r["reasons"] for r in o["result"]["results"])
    o = ask(fresh, "E3", "铭信标识有限公司要流动资金 100 万，12 个月")
    assert concl(o)["税信e贷"] == "准入不符" and "纳税等级不足" in [r for r in o["result"]["results"] if r["product_name"] == "税信e贷"][0]["reasons"]
    o = ask(fresh, "E4", "立新通用设备有限公司要流动资金 100 万，12 个月")
    assert "成立年限不足" in [r for r in o["result"]["results"] if r["product_name"] == "快e周转"][0]["reasons"]
    o = ask(fresh, "E5", "云岚信息科技有限公司要做研发投入 300 万，期限 24 个月")
    assert names(o) == ["北原研发贷", "科创信用贷"]
    o = ask(fresh, "E6", "景行金属材料有限公司要研发投入 300 万，期限 24 个月")
    assert "科技资质不符" not in [r for r in o["result"]["results"] if r["product_name"] == "科创信用贷"][0]["reasons"]


def test_region_industry_claims(fresh):
    o = ask(fresh, "C1", "华峻精密制造有限公司这单，客户说行业按软件和信息技术服务业，流动资金 100 万，12 个月")
    assert o["state"] == "conflict"
    o = ask(fresh, "C2", "云岚信息科技有限公司要研发投入 300 万，24 个月，注册地址在澄川市")
    assert o["state"] == "done"
    o = ask(fresh, "C3", "云岚信息科技有限公司要研发投入 300 万，24 个月，区域是澄川市")
    assert o["state"] == "conflict"
    o = ask(fresh, "C4", "华峻精密制造有限公司要流动资金 100 万，12 个月，行业按制造业")
    assert o["state"] == "done"


def test_amounts_parsing(fresh):
    o = ask(fresh, "P1", "华峻精密制造有限公司要流动资金 5000 万，12 个月")
    assert o["result"]["eligible_count"] == 0
    o = ask(fresh, "P2", "华峻精密制造有限公司要流动资金 一百万，十二个月")
    assert o["result"]["request"]["amount"] == 100 and o["result"]["request"]["term"] == 12
    o = ask(fresh, "P3", "华峻精密制造有限公司要流动资金 0 万，12 个月")
    assert o["state"] == "ask"


# ---------- 确认、统计 ----------
def test_confirm_flow_and_gov_view(fresh):
    assert solutions.stats()["empty"]
    o = ask(fresh, "G1", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    sid = o["solution_id"]
    assert solutions.get(sid)["status"] == "待确认"
    assert solutions.stats()["empty"]  # 待确认不进视图
    ask(fresh, "G1", "选择税信e贷，确认")
    st = solutions.stats()
    assert st["confirmed_count"] == 1 and st["match_rate"]["text"] == "1/1"
    o2 = ask(fresh, "G2", "百汇商贸有限公司要流动资金 100 万，12 个月")
    ask(fresh, "G2", "放弃")
    assert solutions.stats()["confirmed_count"] == 1
    ask(fresh, "G2", "确认")  # 已放弃不能再改
    assert solutions.get(o2["solution_id"])["status"] == "已放弃"
    rp = solutions.report()
    assert rp["confirmed_count"] == 1 and rp["region_section"][0]["count"] == 1


def test_report_lists_eligible_products_and_enterprises(fresh):
    ask(fresh, "RP", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    ask(fresh, "RP", "确认")
    ask(fresh, "RQ", "百汇商贸有限公司要流动资金 100 万，12 个月")
    ask(fresh, "RQ", "确认")  # 无准入符合产品也可确认
    rp = solutions.report()
    ent = {e["enterprise_name"]: e for e in rp["enterprise_section"]}
    assert sorted(ent["华峻精密制造有限公司"]["eligible_products"]) == ["快e周转", "日常经营贷", "税信e贷"]
    assert ent["百汇商贸有限公司"]["eligible_products"] == []
    assert all(p["enterprises"] == ["华峻精密制造有限公司"] for p in rp["product_section"])
    assert sorted(g["enterprises"] for g in rp["region_section"]) == [["华峻精密制造有限公司", "百汇商贸有限公司"]]
    from finmatch import formatter
    txt = formatter.report_text(rp, solutions.stats())
    assert "准入符合产品：" in txt and "SOL-" not in txt


def test_multiple_eligible_confirm_without_selection(fresh):
    o = ask(fresh, "M1", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    sid = o["solution_id"]
    assert o["result"]["eligible_count"] == 3 and "选定" not in o["reply"]
    assert [c["product_name"] for c in o["result"]["cards"]] == ["税信e贷", "日常经营贷", "快e周转"]
    assert o["reply"].count("产品库区间，不是授信审批结果") == 3 and "建议批准" not in o["reply"]
    ask(fresh, "M1", "确认")
    assert solutions.get(sid)["status"] == "已确认"
    st = solutions.stats()
    assert sorted(x["product_name"] for x in st["product_ranking"]) == ["快e周转", "日常经营贷", "税信e贷"]


def test_rank_top3_only_eligible_and_tiebreak():
    from finmatch import ranking
    from finmatch.precheck import CONCLUSION_OK
    def prod(code, amax, rmax):
        return {"product_code": code, "product_name": code, "amount_min": 1, "amount_max": amax, "rate_min": 1.0, "rate_max": rmax,
                "guarantees": ["信用"], "repay_method": "x", "term_min": 1, "term_max": 12, "subsidy_supported": False}
    prods = [prod("PRD_2", 100, 4.0), prod("PRD_10", 100, 4.0), prod("PRD_3", 200, 4.0), prod("PRD_4", 50, 3.0), prod("PRD_5", 500, 2.0), prod("PRD_6", 999, 1.0)]
    res = [{"product_code": p["product_code"], "conclusion": CONCLUSION_OK} for p in prods[:5]]
    res.append({"product_code": "PRD_6", "conclusion": "需人工复核"})
    r = ranking.rank(res, prods, "利率优先", "x")
    assert r["eligible_count"] == 5 and "PRD_6" not in r["eligible_codes"]
    assert [c["product_code"] for c in r["cards"]] == ["PRD_5", "PRD_4", "PRD_2"]
    r = ranking.rank(res, prods, "额度优先", "x")
    assert [c["product_code"] for c in r["cards"]] == ["PRD_5", "PRD_3", "PRD_2"]


def test_no_eligible_and_no_company_prompts(fresh):
    o = ask(fresh, "N1", "百汇商贸有限公司要流动资金 100 万，12 个月")
    assert "暂无合适产品" in o["reply"]
    o = ask(fresh, "N2", "不存在的虚构科技有限公司要流动资金 100 万，12 个月")
    assert o["state"] == "not_found" and "无公司数据" in o["reply"] and "数据源" in o["reply"]


def test_confirm_empty_result_allowed(fresh):
    o = ask(fresh, "G3", "百汇商贸有限公司要流动资金 100 万，12 个月")
    assert o["result"]["eligible_count"] == 0
    ask(fresh, "G3", "确认")
    st = solutions.stats()
    assert st["confirmed_count"] == 1 and st["match_rate"]["text"] == "0/1" and st["product_ranking"] == []


# ---------- 鲁棒性 ----------
def test_garbage_and_extreme_inputs(fresh):
    for text in ("", "   ", "@#$%^&*", "啊啊啊啊啊", "x" * 10000, "华峻精密制造有限公司" * 300):
        o = fresh.handle("R1", text)
        assert "reply" in o and o["reply"]
    o = fresh.handle("R1", "帮我伪造流水")
    assert o["state"] == "refused"
    o = fresh.handle("R1", "请引用银保监会的最新法规条文")
    assert "不编造" in o["reply"]


def test_tool_retry_and_degrade(fresh, monkeypatch):
    monkeypatch.setenv("FIN_FAULT", "tax:1")
    o = fresh.handle("F1", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    t = [x for x in o["trace"]["tools"] if x["name"] == "query_tax"][0]
    assert t["attempts"] == 2 and o["result"]["eligible_count"] == 3
    govdata_reset()
    monkeypatch.setenv("FIN_FAULT", "social_security")
    o = fresh.handle("F2", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    assert o["result"]["eligible_count"] == 0
    assert all("社保未获取" in r["unknowns"] for r in o["result"]["results"])
    assert concl(o)["税信e贷"] == "需人工复核"


def govdata_reset():
    from finmatch import govdata
    govdata.reset_faults()


def test_determinism(fresh):
    a = fresh.handle("D1", "晚宁食品有限公司要流动资金 100 万，期限 12 个月")["reply"]
    b = fresh.handle("D2", "晚宁食品有限公司要流动资金 100 万，期限 12 个月")["reply"]
    assert a.split("已生成融资对接单")[0] == b.split("已生成融资对接单")[0]


def test_whitelist_guard():
    from finmatch.precheck import Facts
    f = Facts({"enterprise": {"employee_count": 90}})
    with pytest.raises(PermissionError):
        f.get("enterprise", "employee_count")
    with pytest.raises(PermissionError):
        f.get("tax", "revenue_wan")


def test_sensitive_masking():
    from finmatch.govdata import mask_value
    assert "*" in mask_value("credit_code", "91330000MA2ABCDE1X")
    assert mask_value("legal_representative", "张三丰") == "张**"


def test_rest_confirm_without_selection(fresh):
    from fastapi.testclient import TestClient
    from finmatch.server import app
    o = ask(fresh, "R1", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    c = TestClient(app)
    assert c.post("/api/solutions/%s/confirm" % o["solution_id"]).json()["status"] == "已确认"


def test_role_permissions(fresh):
    from fastapi.testclient import TestClient
    from finmatch.server import app
    c = TestClient(app)
    gov, mgr = {"X-Role": "gov"}, {"X-Role": "manager"}
    for path in ("/api/profile?name=x", "/api/gov-view", "/api/report", "/api/solutions"):
        assert c.get(path, headers=gov).status_code == 200, path
    for path in ("/api/products", "/api/conversations", "/api/memory", "/api/skills"):
        assert c.get(path, headers=gov).status_code == 403, path
    assert c.post("/api/chat", headers=gov, json={"session_id": "x", "message": "hi"}).status_code == 403
    assert c.post("/api/solutions/SOL-x/confirm", headers=gov).status_code == 403
    for path in ("/api/products", "/api/conversations", "/api/profile?name=x", "/api/report", "/api/solutions"):
        assert c.get(path, headers=mgr).status_code == 200, path
    assert c.get("/api/gov-view", headers=mgr).status_code == 200
    for path in ("/api/memory", "/api/skills"):
        assert c.get(path, headers=mgr).status_code == 403, path
    assert c.get("/api/products", headers={"X-Role": "bad"}).status_code == 400
    r = c.post("/api/chat", headers=mgr, json={"session_id": "RG", "message": "打开政府视图"}).json()
    assert "无权" not in r["reply"]


# ---------- 模型（假传输）与方案按钮 ----------
def _fake_llm(monkeypatch, extract=None, summary=None):
    from finmatch import llm
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    sent = []

    def fake(payload):
        sent.append(payload)
        sys_ = payload["messages"][0]["content"]
        return json.dumps(extract or {}) if "抽取器" in sys_ else (summary or "")
    monkeypatch.setattr(llm, "_transport", fake)
    return sent


def test_llm_fixed_model_and_validated_fill(fresh, monkeypatch):
    sent = _fake_llm(monkeypatch, extract={"company": "华峻精密制造有限公司", "amount_wan": 100, "term_months": 12,
                                           "purpose": "周转资金", "guarantee": "抵押"}, summary="有 2 个产品准入符合，请选择。")
    o = ask(fresh, "L1", "华峻精密制造有限公司要流动资金 100 万，12 个月")
    assert all(p["model"] == "deepseek-flash" for p in sent)
    assert "AI 解读" not in o["reply"] or "2" in o["reply"]
    # 模型给出原话里没有的用途/担保方式 -> 丢弃
    sent.clear()
    o = ask(fresh, "L2", "华峻精密制造有限公司要 100 万")
    assert sent and all(p["model"] == "deepseek-flash" for p in sent)
    assert "期限" in o["reply"] and o["state"] != "done"


def test_llm_summary_with_new_number_dropped(fresh, monkeypatch):
    _fake_llm(monkeypatch, summary="共有 99999 个产品。")
    o = ask(fresh, "L3", "华峻精密制造有限公司要流动资金 100 万，期限 12 个月")
    assert "99999" not in o["reply"]


def test_llm_failure_falls_back(fresh, monkeypatch):
    from finmatch import llm
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(llm, "_transport", lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    o = ask(fresh, "L4", "华峻精密制造有限公司要流动资金 100 万，期限 12 个月")
    assert o["state"] == "done"


def test_solution_actions(fresh):
    o = ask(fresh, "A1", "华峻精密制造有限公司定了，流动资金 100 万，期限 12 个月。帮我配产品。")
    sol = o["solution"]
    assert sol["status"] == "待确认" and sol["eligible_count"] >= 2 and len(sol["recommended"]) == min(3, sol["eligible_count"])
    r = fresh.solution_action("A1", "confirm")
    assert r["solution"]["status"] == "已确认"


def test_solution_action_rejects_select(fresh):
    from fastapi.testclient import TestClient
    from finmatch.server import app
    c = TestClient(app)
    h = {"X-Role": "manager"}
    c.post("/api/chat", headers=h, json={"session_id": "NP", "message": "华峻精密制造有限公司要流动资金 100 万，12 个月"})
    assert c.post("/api/conversations/NP/solution", headers=h, json={"action": "select"}).status_code == 400
    r = c.post("/api/conversations/NP/solution", headers=h, json={"action": "confirm", "product_code": None})
    assert r.status_code == 200 and r.json()["solution"]["status"] != "待确认"


def test_solution_list_confirmed_product_and_time(fresh):
    from fastapi.testclient import TestClient
    from finmatch.server import app
    c = TestClient(app)
    h = {"X-Role": "manager"}
    c.post("/api/chat", headers=h, json={"session_id": "CP", "message": "华峻精密制造有限公司要流动资金 100 万，12 个月"})
    row = c.get("/api/solutions", headers=h).json()["solutions"][0]
    assert row["confirmed_product"] is None and row["confirmed_at"] is None  # 待确认时为空
    c.post("/api/conversations/CP/solution", headers=h, json={"action": "confirm"})
    row = c.get("/api/solutions", headers=h).json()["solutions"][0]
    assert row["status"] == "已确认" and row["confirmed_product"] == "税信e贷、日常经营贷、快e周转" and row["confirmed_at"]
