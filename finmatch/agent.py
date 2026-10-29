"""对话 Agent：解析 → 缺项追问 → 分次调取政务数据 → 预审 → 排序 → 建单 → 确认/放弃 → 视图与报告。
预审、排序、统计全部在工具与服务里完成，这里只做编排。"""
import re
from datetime import datetime

from . import config, context, db, flow as FL, formatter as F, govdata, llm, memory, nlu, obs, products as P, ranking, skills, solutions
from .subagents import DataAgent, PrecheckAgent, RankAgent, ReflectAgent
from .precheck import precheck_all, Facts, CONCLUSION_OK
from .tools import CURRENT, Trace, call_tool

MAX_INPUT = 4000
LABELS = {"amount": "融资金额（万元）", "term": "期限（几个月）", "purpose": "用途"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _vals(res):
    return {c: f["value"] for c, f in res["facts"].items()}


def _s_enterprise(r):
    if not r["found"]:
        return "未命中：库内没有名称完全一致的企业"
    v = _vals(r)
    return "命中 %s，成立日期 %s，经营状态 %s，区域 %s，行业 %s，数据授权 %s" % (
        r["enterprise_id"], v["founded_date"], v["business_status"], v["region"], v["industry"], v["data_authorization"])


def _yn(v):
    return "未获取" if v is None else ("是" if v == 1 else "否")


def _s_tax(r):
    v = _vals(r)
    return "纳税等级 %s，纳税人资格 %s，欠缴 %s，近12个月纳税额 %s 万元" % (
        v["tax_credit_rating"] or "未获取", v["taxpayer_type"] or "未获取", _yn(v["tax_arrears"]), "未获取" if v["tax_paid_12m_wan"] is None else v["tax_paid_12m_wan"])


def _s_ss(r):
    v = _vals(r)
    return "参保 %s 人，欠缴 %s" % ("未获取" if v["insured_count"] is None else v["insured_count"], _yn(v["social_security_arrears"]))


def _s_jud(r):
    v = _vals(r)
    return "严重失信 %s，被执行 %s" % (_yn(v["is_dishonest"]), _yn(v["is_enforced"]))


def _s_fh(r):
    v = _vals(r)
    return "%d 笔历史融资，逾期未结清 %s" % (r["count"], _yn(v["overdue_unsettled"]) if r["count"] else "无记录，按无逾期处理")


DATA = DataAgent([
    ("query_tax", govdata.query_tax, _s_tax, "tax"),
    ("query_social_security", govdata.query_social_security, _s_ss, "social_security"),
    ("query_judicial", govdata.query_judicial, _s_jud, "judicial"),
    ("query_financing_history", govdata.query_financing_history, _s_fh, "financing_history"),
], _vals)
PRECHECK, RANKER, REFLECT = PrecheckAgent(), RankAgent(), ReflectAgent()


class Agent:
    # ---------- 会话 ----------
    def _ctx(self, sid):
        with db.connect() as c:
            r = c.execute("SELECT context FROM sessions WHERE session_id=?", (sid,)).fetchone()
        if r:
            return db.loads(r["context"], {})
        return {"company": None, "slots": {}, "current_solution": None}

    def _save_ctx(self, sid, ctx):
        ts = _now()
        with db.connect() as c:
            c.execute(
                "INSERT INTO sessions(session_id,context,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET context=excluded.context, updated_at=excluded.updated_at",
                (sid, db.dumps(ctx), ts, ts))

    def _title(self, sid, ctx, created=None):
        """会话名称 = 公司名称 + 新建会话日期；还没识别到企业时用「新会话」。"""
        if created is None:
            with db.connect() as c:
                r = c.execute("SELECT created_at FROM sessions WHERE session_id=?", (sid,)).fetchone()
            created = r["created_at"] if r else _now()
        return "%s · %s" % (ctx.get("company") or "新会话", (created or "")[:10])

    def _log(self, sid, role, content, trace=None):
        with db.connect() as c:
            c.execute("INSERT INTO messages(session_id,role,content,trace,created_at) VALUES(?,?,?,?,?)",
                      (sid, role, content, db.dumps(trace) if trace else None, _now()))

    def history(self, sid, limit=None):
        """按会话隔离读取；limit 只取最近 N 条（页面回看和送模型都不读全量）。"""
        with db.connect() as c:
            if limit:
                rows = c.execute("SELECT role,content,trace,created_at FROM (SELECT id,role,content,trace,created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id", (sid, limit)).fetchall()
            else:
                rows = c.execute("SELECT role,content,trace,created_at FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
        return [{"role": r["role"], "content": r["content"], "trace": db.loads(r["trace"]), "at": r["created_at"]} for r in rows]

    def list_sessions(self):
        with db.connect() as c:
            return [dict(r) for r in c.execute("SELECT session_id, created_at, updated_at FROM sessions ORDER BY created_at")]

    # ---------- 入口 ----------
    def _begin(self, sid):
        """每个回合一条调用链：trace + 状态机；CURRENT 让模型层把 token/成本记到这一回合上。"""
        trace = Trace(sid)
        trace.fsm = FL.Flow(trace)
        return trace, CURRENT.set(trace)

    def _finish(self, sid, trace, token, out, status_hint=None):
        try:
            trace.fsm.end(out.get("state") or status_hint or "done")
        except Exception as e:
            trace.fail(trace.node or "END", "orchestrator", e)
        CURRENT.reset(token)
        out["trace"] = trace.to_dict()
        out["trace_id"] = trace.trace_id
        ctx = self._ctx(sid)
        out["pending_auth"] = ctx.get("pending_auth")
        out["title"] = self._title(sid, ctx)
        out["solution"] = self.solution_view(sid)
        self._log(sid, "assistant", out["reply"], out["trace"])
        obs.persist(sid, out["trace"])
        self._compact(sid)
        return out

    def _compact(self, sid):
        """消息过多时压缩旧消息进会话摘要；失败不影响本回合。"""
        try:
            ctx = self._ctx(sid)
            if context.maybe_compact(ctx, self.history(sid)):
                self._save_ctx(sid, ctx)
        except Exception:
            pass

    # ---------- 入口 ----------
    def handle(self, sid, text, role=None):
        trace, token = self._begin(sid)
        text = (text or "")
        if len(text) > MAX_INPUT:
            trace.notes.append("输入超长（%d 字），只处理前 %d 字" % (len(text), MAX_INPUT))
            text = text[:MAX_INPUT]
        self._log(sid, "user", text)
        try:
            trace.fsm.go("PARSE")
            out = self._handle(sid, text, trace, role)
        except Exception as e:  # 单点异常不让整条链路崩溃，且定位到出错节点
            node = trace.node or "UNKNOWN"
            trace.fail(node, "orchestrator", e)
            trace.notes.append("内部异常：%s: %s" % (type(e).__name__, e))
            out = {"reply": "这一步处理时出了内部错误（节点 %s，%s），没有生成预审结果，也没有改动任何方案。调用链编号 %s，可在可观测接口里还原。请换个说法重试，或检查输入。" % (node, type(e).__name__, trace.trace_id), "state": "error"}
        return self._finish(sid, trace, token, out)

    def _handle(self, sid, text, trace, role=None):
        ctx = self._ctx(sid)
        stripped = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
        if not stripped:
            trace.rule("输入校验=空白或无有效字符")
            return {"reply": "没有收到有效内容。请告诉我企业名称、融资金额（万元）、期限（几个月）和用途，例如：「华峻精密制造有限公司要流动资金 100 万，12 个月」。", "state": "need_input"}
        if nlu.IMPROPER.search(text):
            trace.rule("内容合规=拒绝不当请求")
            return {"reply": "这类请求涉及造假、规避风控或违规操作，我不能协助。预审只依据已授权的政务数据和产品说明书条款进行。", "state": "refused"}

        pa = ctx.get("pending_auth")
        verdict = nlu.parse_auth(text) if pa else None
        if verdict:
            trace.plan = FL.make_plan(True, False, False)
            trace.fsm.go("ROUTE", "授权回复")
            return self._auth_decision(sid, ctx, verdict, trace)

        db_names = govdata.list_enterprise_names()
        vocab = P.purpose_vocabulary()
        industries = govdata.distinct_enterprise_values("industry")
        sents = [nlu.parse_sentence(s, db_names, vocab, industries) for s in nlu.split_sentences(text)]
        proposal = None
        if llm.enabled() and len(sents) == 1:
            with trace.span("PARSE", "llm_route"):
                proposal = llm.route(text, {"company": ctx.get("company"), "has_pending_solution": bool(ctx.get("current_solution")),
                                            "pending_auth": bool(ctx.get("pending_auth"))})
            if proposal is None:
                trace.rule("意图识别=模型无有效提议，回退规则")
            with trace.span("PARSE", "llm_extract"):
                self._llm_fill(sents[0], ctx, text, db_names, vocab, trace, self.history(sid, 40)[:-1])
        else:
            trace.rule("对话解析=规则解析（本地，不外送）")

        # 分组：每出现一个企业名称开一组；其余句子并入当前组
        groups, cur = [], None
        for s in sents:
            if s["company"] and (cur is None or cur["company"] is not None):
                cur = {"company": None, "sents": []}
                groups.append(cur)
            elif cur is None:
                cur = {"company": None, "sents": []}
                groups.append(cur)
            if s["company"]:
                cur["company"] = s["company"]
            cur["sents"].append(s)

        flags = {k: any(s[k] for s in sents) for k in ("gov", "report", "profile", "memory_show", "memory_clear", "recall", "regulation")}
        replies, structured = [], []
        has_demand = any(s["company"] or s["amount"] is not None or s["term"] is not None or s["purpose"] or s["amount_issue"] or s["term_issue"] for s in sents)
        has_act = any(s[k] for s in sents for k in ("confirm", "abandon", "hold"))
        has_info = any(flags[k] for k in ("gov", "report", "profile", "memory_show", "recall"))
        route_note = None
        if proposal:
            has_demand, has_info, route_note = self._adjudicate(proposal, flags, has_demand, has_act, has_info, trace)
        trace.plan = FL.make_plan(has_demand, has_act, has_info)
        trace.fsm.go("ROUTE", "需求%s 操作%s 查询%s" % (int(has_demand), int(has_act), int(has_info)))

        if flags["memory_clear"]:
            n = memory.clear_all()
            trace.tools.append({"name": "memory_clear", "args": {}, "result_summary": "清除 %d 条记忆" % n})
            ctx = {"company": None, "slots": {}, "current_solution": None}
            self._save_ctx(sid, ctx)
            return {"reply": "已清除记忆（%d 条）和本会话的上下文。预审口径默认值仍按系统配置。" % n, "state": "memory_cleared"}

        multi = sum(1 for g in groups if g["company"]) > 1
        for g in groups:
            r = self._run_group(sid, ctx, g, trace, compact=multi, profile_only=flags["profile"])
            if r:
                replies.append(r["reply"])
                structured.append(r)
        if has_info and trace.fsm.cur != "INFO":
            trace.fsm.go("INFO")
        if flags["memory_show"]:
            items = memory.list_all()
            trace.tools.append({"name": "memory_list", "args": {}, "result_summary": "%d 条" % len(items)})
            replies.append("当前记忆（可在「记忆」面板纠正或清除）：\n" + "\n".join("- [%s] %s：%s" % (m["scope"], m["key"], m["value"]) for m in items))
        if flags["recall"] and not structured:
            m = memory.get("session:" + sid, "last_precheck")
            replies.append("上一单结论：" + m if m else "本会话还没有预审结论可以复用。")
        if flags["profile"]:
            name = ctx.get("company")
            replies.append(("企业画像已生成，见「企业画像」页（企业：%s）。" % name) if name else "请先告诉我企业名称，再看企业画像。")
        if flags["gov"] or flags["report"]:
            trace.skill(skills.name("gov_stats"))
            st = solutions.stats()
            trace.tools.append({"name": "gov_view_stats", "args": {}, "result_summary": "已确认 %d 笔" % st["confirmed_count"]})
            trace.rule("统计口径=只统计已确认方案")
            mine = solutions.list_solutions(session_id=sid)
            pend = [s["enterprise_name"] for s in mine if s["status"] == solutions.PENDING]
            aban = [s["enterprise_name"] for s in mine if s["status"] == solutions.ABANDONED]
            if flags["gov"]:
                replies.append(F.gov_text(st, pend, aban))
            if flags["report"]:
                rp = solutions.report()
                replies.append(F.report_text(rp, st))
        if flags["regulation"] and not structured:
            trace.rule("内容合规=不编造法规条款")
            replies.append("我不提供、也不编造法规或标准条文。预审的依据只有两类：已授权的政务数据，以及已入库产品说明书的第三条至第六条。需要引用法规请以官方发布文本为准。")
        if route_note and not structured:
            replies.append(route_note)
        if not replies:
            replies.append(self._help(ctx))
        self._save_ctx(sid, ctx)
        out = {"reply": "\n\n".join(replies), "state": structured[-1]["state"] if structured else "info"}
        if structured:
            out["result"] = structured[-1].get("result")
            out["solution_id"] = structured[-1].get("solution_id")
        return out

    def _adjudicate(self, proposal, flags, has_demand, has_act, has_info, trace):
        """模型提议、代码裁决：
        - 只读意图（政府视图、报告、画像、记忆、上一单结论）和「提出需求」可采纳，采纳后仍走原有流水线与硬规则；
        - 确认、放弃、暂不确认会改变方案状态，规则没从原话识别出时不执行，只提示用户明确表述或点按钮；
        - 工具必须落在所提意图的允许范围内，超出的丢弃。执行的流程仍由状态机决定。"""
        accepted, rejected, note = [], [], None
        for i in proposal["intents"]:
            if i == "other":
                continue
            if i == "demand":
                has_demand = True
                accepted.append(i)
            elif i in ("gov", "report", "profile", "memory_show", "recall"):
                if not flags.get(i):
                    flags[i] = True
                    has_info = True
                accepted.append(i)
            elif has_act:
                accepted.append(i)
            else:
                rejected.append(i)
                note = "我理解你想「%s」方案，但这会改变方案状态，请直接回复「确认」「放弃」，或点方案卡片上的按钮。" % {"confirm": "确认", "abandon": "放弃", "hold": "暂不确认"}[i]
        trace.tools.append({"name": "llm_route", "args": {"model": llm.DISPLAY},
                            "result_summary": "采纳意图 %s；拒绝 %s；工具 %s；丢弃工具 %s；依据：%s" % (
                                accepted or "无", rejected or "无", proposal["tools"] or "无", proposal["dropped_tools"] or "无", proposal["reason"] or "无")})
        trace.rule("意图识别=模型 %s 提议，代码裁决（只读意图与需求采纳；改变方案状态的意图须规则识别）" % llm.DISPLAY)
        return has_demand, has_info, note

    def _llm_fill(self, sent, ctx, text, db_names, vocab, trace, history=()):
        """模型只补规则没识别出、也没判定为有问题的字段；规则已有的值不动。"""
        have = {"company": bool(sent["company"]), "amount": sent["amount"] is not None or bool(sent["amount_issue"]),
                "term": sent["term"] is not None or bool(sent["term_issue"]),
                "purpose": bool(sent["purpose"] or sent["purpose_multi"]),
                "guarantee": sent["guarantee"] is not None or bool(sent["guarantee_issue"]),
                "primary": sent["primary"] is not None}
        skip = {k for k, v in have.items() if v}
        sl = ctx.get("slots") or {}
        need = [k for k in ("company", "amount", "term", "purpose") if not have[k] and not (ctx.get("company") if k == "company" else sl.get(k))]
        if not need:
            trace.rule("对话解析=规则与会话记忆已覆盖必填项，不调用模型")
            return
        got = llm.extract(text, ctx.get("company"), db_names, vocab, skip, context.llm_context(ctx, list(history)))
        trace.tools.append({"name": "llm_extract", "args": {"model": llm.DISPLAY}, "result_summary": ("补全 " + "、".join(got)) if got else "无补全（未返回或未通过校验）"})
        trace.rule("对话解析=规则优先，模型 %s 仅补缺并经代码校验" % llm.DISPLAY)
        if "company" in got:
            sent["company"] = ("found", got.pop("company"))
        sent.update(got)

    def _explain(self, trace, result):
        trace.fsm.go("EXPLAIN")
        with trace.span("EXPLAIN", "explain_agent"):
            return self._llm_summary(result, trace)

    def _llm_summary(self, result, trace):
        results = result.get("results", [])
        ok = [r["product_name"] for r in result.get("cards", [])]
        view = {"request": result.get("request"), "eligible_count": result.get("eligible_count"), "eligible": ok,
                "rejected": [{"product": r["product_name"], "reasons": context.clip_list(r["reasons"], 4)} for r in results if r["conclusion"] != "准入符合"]}
        note = llm.summarize(view)
        trace.tools.append({"name": "llm_summary", "args": {"model": llm.DISPLAY}, "result_summary": "已附加" if note else "未返回或未通过校验，已省略"})
        return note

    def _auth_decision(self, sid, ctx, verdict, trace):
        """授权工具的统一入口：对话里说「已授权/不授权」和页面按钮都走这里。"""
        pa = ctx.get("pending_auth")
        if not pa:
            return {"reply": "当前没有待授权的企业。", "state": "info"}
        ctx.pop("pending_auth", None)
        if verdict == "deny":
            ctx["current_solution"] = None
            self._save_ctx(sid, ctx)
            trace.rule("授权检查=用户未授权，不调取税务、社保、司法和历史融资")
            return {"reply": "已记录：「%s」未授权。我没有调取任何数据，也不会做预审。企业授权后再告诉我即可。" % pa["name"], "state": "unauthorized"}
        govdata.grant_authorization(pa["enterprise_id"], _now())
        trace.tools.append({"name": "grant_authorization", "args": {"enterprise_id": pa["enterprise_id"]}, "result_summary": "数据授权 → 已授权"})
        trace.rule("授权检查=用户已授权，继续预审")
        r = self._demand(sid, ctx, [], trace, False, False)
        self._save_ctx(sid, ctx)
        out = {"reply": "已记录「%s」的数据授权，继续处理。\n%s" % (pa["name"], r["reply"]), "state": r.get("state", "info")}
        for k in ("result", "solution_id"):
            if k in r:
                out[k] = r[k]
        return out

    def authorize(self, sid, decision):
        """页面授权按钮。decision: grant / deny。"""
        trace, token = self._begin(sid)
        label = "已授权" if decision == "grant" else "不授权"
        self._log(sid, "user", "（页面操作）%s" % label)
        try:
            trace.plan = FL.make_plan(True, False, False)
            trace.fsm.go("PARSE")
            trace.fsm.go("ROUTE", "页面授权")
            out = self._auth_decision(sid, self._ctx(sid), decision, trace)
        except Exception as e:
            trace.fail(trace.node or "UNKNOWN", "orchestrator", e)
            out = {"reply": "授权后继续处理时出错（节点 %s，%s），调用链编号 %s。" % (trace.node, type(e).__name__, trace.trace_id), "state": "error"}
        return self._finish(sid, trace, token, out)

    def new_session(self):
        sid = "S%s" % datetime.now().strftime("%y%m%d%H%M%S%f")
        self._save_ctx(sid, {"company": None, "slots": {}, "current_solution": None})
        return sid

    def conversations(self):
        """会话记录：名称为「公司名称 · 新建日期」，最近更新的在前；附轮数与最后一句，供页面展示。"""
        with db.connect() as c:
            rows = c.execute(
                "SELECT s.session_id, s.context, s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.session_id=s.session_id AND m.role='user') AS turns, "
                "(SELECT content FROM messages m WHERE m.session_id=s.session_id AND m.role='user' ORDER BY m.id DESC LIMIT 1) AS last_user "
                "FROM sessions s ORDER BY s.updated_at DESC, s.session_id DESC").fetchall()
        out = []
        for r in rows:
            ctx = db.loads(r["context"], {})
            out.append({"session_id": r["session_id"], "title": self._title(r["session_id"], ctx, r["created_at"]),
                        "company": ctx.get("company"), "created_at": r["created_at"], "updated_at": r["updated_at"],
                        "turns": r["turns"], "last": (r["last_user"] or "")[:40],
                        "pending_auth": bool(ctx.get("pending_auth"))})
        return out

    def conversation(self, sid):
        ctx = self._ctx(sid)
        return {"session_id": sid, "title": self._title(sid, ctx), "company": ctx.get("company"),
                "pending_auth": ctx.get("pending_auth"),
                "messages": self.history(sid, 200), "solution": self.solution_view(sid)}

    def solution_view(self, sid):
        """当前会话待操作方案的视图，供页面渲染确认/放弃按钮。"""
        sol_id = self._ctx(sid).get("current_solution")
        sol = solutions.get(sol_id) if sol_id else None
        if not sol:
            return None
        rec = sol["recommended"] or {}
        names = {p["product_code"]: p["product_name"] for p in P.list_products()}
        return {"id": sol_id, "status": sol["status"], "enterprise": sol["enterprise_name"],
                "eligible_count": sol["eligible_count"],
                "recommended": [{"code": c["product_code"], "name": c["product_name"]} for c in rec.get("cards", [])]}

    def solution_action(self, sid, action, code=None):
        """页面按钮：confirm / abandon。逻辑与对话里的「确认/放弃」一致。"""
        trace, token = self._begin(sid)
        ctx = self._ctx(sid)
        sol_id = ctx.get("current_solution")
        sol = solutions.get(sol_id) if sol_id else None
        label = {"confirm": "确认", "abandon": "放弃"}.get(action)
        if not label:
            CURRENT.reset(token)
            raise ValueError("未知操作")
        trace.fsm.go("PARSE")
        trace.fsm.go("ROUTE", "页面操作")
        trace.fsm.go("ACT")
        self._log(sid, "user", "（页面操作）%s" % label)
        try:
            return self._do_action(sid, ctx, action, code, sol_id, sol, trace, token)
        except Exception as e:
            trace.fail(trace.node or "ACT", "orchestrator", e)
            return self._finish(sid, trace, token, {"reply": "操作失败（节点 %s，%s），调用链编号 %s。" % (trace.node, type(e).__name__, trace.trace_id), "state": "error"})

    def _do_action(self, sid, ctx, action, code, sol_id, sol, trace, token):
        reply = self._act(sid, ctx, {"confirm": action == "confirm", "abandon": action == "abandon"}, sol_id, trace, False)
        return self._finish(sid, trace, token, {"reply": reply, "state": "act"})

    def _help(self, ctx):
        s = "我可以做融资预审：请告诉我企业名称（须与工商登记完全一致）、融资金额（万元）、期限（几个月）和用途。"
        if ctx.get("company"):
            s += "当前企业：%s。" % ctx["company"]
        return s + "也可以说「确认」「放弃」「打开政府视图」「分析报告」。"

    # ---------- 一组需求 ----------
    def _run_group(self, sid, ctx, g, trace, compact, profile_only=False):
        sents = g["sents"]
        slots = dict(ctx.get("slots", {}))
        dirty, notes = False, []
        new_company = False
        if g["company"]:
            name = g["company"][1]
            if name != ctx.get("company"):
                new_company = True
                slots.pop("region_claim", None)
                slots.pop("industry_claim", None)
                ctx.pop("pending_auth", None)
            ctx["company"] = name
            ctx["company_status"] = g["company"][0]
            dirty = True
        for s in sents:
            for key, issue_key in (("amount", "amount_issue"), ("term", "term_issue")):
                if s[issue_key]:
                    notes.append((key, s[issue_key]))
                    slots.pop(key, None)
                    dirty = True
                elif s[key] is not None:
                    slots[key] = s[key]
                    dirty = True
            if s["purpose_multi"]:
                notes.append(("purpose", "multi:" + "、".join(s["purpose_multi"])))
                slots.pop("purpose", None)
                dirty = True
            elif s["purpose"]:
                slots["purpose"] = s["purpose"]
                dirty = True
            if s["guarantee_issue"] == "negated":
                notes.append(("guarantee", "negated"))
                dirty = True
            elif s["guarantee"]:
                slots["guarantee"] = s["guarantee"]
                dirty = True
            if s["primary"]:
                slots["primary"] = s["primary"]
                slots["primary_explicit"] = True
                dirty = True
            if s["region_claim"]:
                slots["region_claim"] = s["region_claim"]
                dirty = True
            if s["industry_claim"]:
                slots["industry_claim"] = s["industry_claim"]
                dirty = True
        ctx["slots"] = slots
        act = {k: any(s[k] for s in sents) for k in ("confirm", "abandon", "hold")}

        result = None
        has_slots = any(k in slots for k in LABELS) and any(s[k] is not None for s in sents for k in ("amount", "term", "purpose"))
        if profile_only and not has_slots and not notes:
            dirty = False
        if dirty and ctx.get("company"):
            result = self._demand(sid, ctx, notes, trace, compact, new_company)
        elif dirty and not ctx.get("company"):
            miss = ["企业名称"] + [LABELS[k] for k in LABELS if k not in slots]
            result = {"reply": "请先告诉我企业名称（须与工商登记完全一致），还缺：" + "、".join(miss) + "。", "state": "ask"}
        if result is not None:
            if result.get("state") == "done" and not compact and llm.enabled():
                note = self._explain(trace, result)
                if note:
                    result["reply"] += "\n\nAI 解读（%s）：%s" % (llm.DISPLAY, note)
            trace.fsm.end(result.get("state") or "done")
        reply_parts = [result["reply"]] if result else []

        if result is None:
            sol_id = ctx.get("current_solution")
        elif result.get("state") == "done":
            sol_id = result["solution_id"]
        else:
            sol_id = None
        wants_act = bool(act["confirm"] or act["abandon"] or act["hold"])
        if sol_id and (result is None or result.get("state") != "done"):
            if wants_act:
                trace.fsm.go("ACT")
        if wants_act:
            if trace.fsm.cur != "ACT":
                trace.fsm.go("ACT")
            reply_parts.append(self._act(sid, ctx, act, sol_id, trace, False))
        if trace.fsm.cur == "ACT":
            trace.fsm.end("act")
        if not reply_parts:
            return None
        out = dict(result) if result else {"state": "act"}
        out["reply"] = "\n".join(reply_parts)
        return out

    def _act(self, sid, ctx, act, sol_id, trace, short):
        trace.skill(skills.name("save_solution"))
        if not sol_id:
            return "当前没有可以确认或放弃的预审结果（需求未录全、企业未收录、待授权或材料不一致时不生成方案）。"
        sol = solutions.get(sol_id)
        if act["abandon"]:
            status, changed = solutions.set_status(sol_id, solutions.ABANDONED)
            trace.tools.append({"name": "save_solution", "args": {"solution_id": sol_id, "action": "放弃"}, "result_summary": "状态 %s" % status})
            trace.rule("确认前状态=待确认；放弃后不进入政府视图")
            return "已放弃方案 %s（%s），不进入政府视图和分析报告。" % (sol_id, sol["enterprise_name"]) if changed else "方案 %s 已经是「%s」，不再变更。" % (sol_id, status)
        if act["confirm"]:
            status, changed = solutions.set_status(sol_id, solutions.CONFIRMED)
            trace.tools.append({"name": "save_solution", "args": {"solution_id": sol_id, "action": "确认"}, "result_summary": "状态 %s" % status})
            trace.rule("确认后进入政府视图与报告；确认不提交贷款申请")
            if sol["eligible_count"] == 0 and changed:
                extra = "暂无合适产品，该方案按规则仍允许确认。"
            else:
                extra = ""
            if changed:
                memory.put("global", "已确认:%s" % sol_id, "%s，准入符合 %d 个（%s）" % (sol["enterprise_name"], sol["eligible_count"], sol["primary_condition"]), "confirm")
                return "已保存方案 %s，状态：已确认。%s该方案已进入政府视图与分析报告。本次未提交贷款申请，也不会发送给银行。" % (sol_id, extra)
            return "方案 %s 已经是「%s」，不再变更。" % (sol_id, status)
        return "方案 %s 保持「待确认」，暂不进入政府视图和报告。" % sol_id

    # ---------- 单个需求的完整预审 ----------
    def _demand(self, sid, ctx, notes, trace, compact, new_company):
        trace.fsm.go("COLLECT")
        slots, name = ctx["slots"], ctx["company"]
        ask = []
        for key, issue in notes:
            if key == "amount":
                ask.append("融资金额没识别清楚（出现多个金额或数值无效），请只给一个金额，单位万元" if issue != "invalid" else "融资金额必须大于 0，请重新给金额（万元）")
            elif key == "term":
                if issue == "unit":
                    ask.append("期限只接受「个月」。你说的是天或年，请直接告诉我几个月（我不会把 360 天或 1 年自己折成 12 个月）")
                elif issue == "ambiguous":
                    ask.append("期限出现了多个月数，请只给一个")
                else:
                    ask.append("期限请给正整数个月")
            elif key == "purpose":
                ask.append("用途出现了多个（%s），请只选一个，用词与产品条款一致" % issue.split(":", 1)[1])
            elif key == "guarantee":
                ask.append("担保方式请直接说明要哪一种（信用、抵押、质押、保证），不要用否定说法")
        missing = [LABELS[k] for k in LABELS if k not in slots and not any(n[0] == k for n in notes)]
        if ask or missing:
            trace.rule("缺项追问=先追问，再调数据、再匹配")
            lines = ["先补齐信息再预审（企业：%s）：" % name]
            lines.extend("· " + a for a in ask)
            if missing:
                lines.append("· 还缺：" + "、".join(missing))
            return {"reply": "\n".join(lines), "state": "ask"}

        req = {"amount": slots["amount"], "term": slots["term"], "purpose": slots["purpose"], "guarantee": slots.get("guarantee")}
        primary = slots.get("primary") or config.RULES["default_primary"]
        explicit = bool(slots.get("primary_explicit"))
        trace.skill(skills.name("collect_gov_data"))

        trace.fsm.go("LOOKUP")
        ok, ent = call_tool(trace, "query_enterprise", govdata.query_enterprise, {"name": name}, _s_enterprise)
        if not ok:
            return {"reply": "企业库查询失败（已重试），本次没有生成预审结果，也没有编造企业数据。请稍后重试。", "state": "error"}
        if not ent["found"]:
            trace.rule("企业名称=与 enterprise.enterprise_name 完全一致才算收录，不模糊匹配")
            ctx["current_solution"] = None
            return {"reply": "库里没有「%s」，按未收录处理。这单不出企业画像，也不匹配产品。工商、税务、社保我都不编。企业名称必须与工商登记完全一致，简称、漏字、多写「集团」或把「有限公司」改成「股份有限公司」都按另一家处理；请核对后重新告诉我。\n无公司数据：请提供「%s」的数据源（工商、税务、社保、司法、历史融资），数据接入后才能预审。" % (name, name), "state": "not_found"}
        trace.fsm.go("AUTH")
        ev = _vals(ent)
        ctx["enterprise_id"] = ent["enterprise_id"]
        trace.rule("授权检查=%s" % ev["data_authorization"])
        if not ent["authorized"]:
            ctx["current_solution"] = None
            ctx["pending_auth"] = {"enterprise_id": ent["enterprise_id"], "name": name}
            return {"reply": "「%s」的数据授权状态是「%s」，整单结论：待授权。我没有调取税务、社保、司法和历史融资，也没有做预审。\n需要企业先授权才能继续：企业已授权的话，请回复「已授权」，我会记录授权并按刚才的条件继续预审；不授权请回复「不授权」。" % (name, ev["data_authorization"] or "空"), "state": "unauthorized"}

        trace.fsm.go("CONSISTENCY")
        conflicts = []
        if slots.get("region_claim") and slots["region_claim"] != ev["region"]:
            conflicts.append("区域（你说的是%s，工商区域是%s）" % (slots["region_claim"], ev["region"]))
        if slots.get("industry_claim") and slots["industry_claim"] != ev["industry"]:
            conflicts.append("行业（你说的是%s，工商行业是%s）" % (slots["industry_claim"], ev["industry"]))
        if conflicts:
            trace.rule("材料一致性=对话区域/行业须与 enterprise.region/industry 一致，不一致则停止匹配")
            ctx["current_solution"] = None
            return {"reply": "这单材料不一致，我停止匹配。%s。我不按你说的去推产品，也不按工商信息继续出名单；请核对后确认以哪边为准（以工商登记为准的话，请重新说明）。" % "；".join(conflicts), "state": "conflict"}

        trace.fsm.go("FETCH")
        data = {"enterprise": ev}
        facts = Facts(data)
        fh_count = DATA.fetch(trace, ent["enterprise_id"], facts, data)
        trace.rule("成立年限=按周年计算，基准日 %s" % config.BASE_DATE.isoformat())
        trace.rule("预审字段=仅 rules.json 白名单列")

        prods = P.list_products()
        trace.tools.append({"name": "query_product_table", "args": {}, "result_summary": "读取已入库产品 %d 条（不读 PDF）" % len(prods)})
        if not prods:
            return {"reply": "产品表为空：还没有上传并入库任何产品说明书，无法预审。请先在产品管理页上传 PDF。", "state": "error"}
        trace.skill(skills.name("run_precheck"))
        trace.fsm.go("PRECHECK")
        results, hit = PRECHECK.run(trace, prods, facts, req)
        for label in hit:
            trace.rule("命中规则：%s（%d 个产品）" % (label, hit[label]))
        trace.skill(skills.name("rank_recommend"))
        trace.fsm.go("RANK")
        rk = RANKER.run(trace, results, prods, primary, ev["industry"])
        trace.fsm.go("REFLECT")
        issues = REFLECT.check(trace, results, rk, req, len(prods))
        while issues and trace.fsm.reflects < FL.MAX_REFLECT:
            trace.rule("自检未通过，重做一次预审与排序：" + "；".join(issues))
            trace.fsm.go("PRECHECK", "重做")
            results, hit = PRECHECK.run(trace, prods, facts, req)
            trace.fsm.go("RANK")
            rk = RANKER.run(trace, results, prods, primary, ev["industry"])
            trace.fsm.go("REFLECT")
            issues = REFLECT.check(trace, results, rk, req, len(prods))
        if issues:
            trace.rule("自检仍未通过，降级：有疑问的产品改判需人工复核。" + "；".join(issues))
            trace.notes.append("自检降级：" + "；".join(issues))
            results = REFLECT.degrade(results, issues)
            rk = RANKER.run(trace, results, prods, primary, ev["industry"])
        trace.rule("排序=%s（%s）" % (primary, "利率上限↑、额度上限↑、编号" if primary == "利率优先" else "额度上限↓、利率上限↑、编号"))
        trace.tools.append({"name": "rank_products", "args": {"primary": primary}, "result_summary": "准入符合 %d 个，推荐 %s" % (rk["eligible_count"], "、".join(c["product_name"] for c in rk["cards"]) or "无")})

        failed_sources = sorted(facts.failed)
        enterprise_info = {"enterprise_id": ent["enterprise_id"], "name": name, "region": ev["region"], "industry": ev["industry"]}
        snapshot = {"results": results, "facts": data, "failed_sources": failed_sources}
        trace.fsm.go("SAVE")
        cur = solutions.get(ctx.get("current_solution")) if ctx.get("current_solution") else None
        if cur and cur["status"] == solutions.PENDING and cur["enterprise_id"] == ent["enterprise_id"] and not new_company:
            solutions.update_precheck(cur["solution_id"], req, primary, snapshot, rk)
            sol_id = cur["solution_id"]
        else:
            sol_id = solutions.create(sid, enterprise_info, req, primary, snapshot, rk)
        ctx["current_solution"] = sol_id
        trace.tools.append({"name": "save_solution", "args": {"solution_id": sol_id}, "result_summary": "状态 待确认"})
        trace.skill(skills.name("save_solution"))

        summary = "%s｜%s 万元｜%s 个月｜%s｜%s｜准入符合 %d 个：%s" % (name, "%g" % req["amount"], req["term"], req["purpose"], primary, rk["eligible_count"], "、".join(c["product_name"] for c in rk["cards"]) or "无")
        memory.put("session:" + sid, "last_precheck", summary, "precheck")
        memory.put("global", "预审:" + name, summary, "precheck")

        notes_extra = []
        if failed_sources:
            notes_extra.append("提示：%s 调取失败（已重试），相关字段按未获取处理，受影响的产品只会判为需人工复核或准入不符并注明未获取。" % "、".join(failed_sources))
        if req["purpose"] not in P.purpose_vocabulary():
            notes_extra.append("提示：用途「%s」没有出现在任何已入库产品的适用用途里（用词须与条款完全一致），所以相关产品都会因用途不符被否决。" % req["purpose"])
        if compact:
            text = F.compact_reply(name, req, primary, results, rk, sol_id, "待确认")
        else:
            text = F.precheck_reply(name, req, primary, explicit, results, rk, data, fh_count, sol_id, notes_extra)
        result = {"enterprise": enterprise_info, "request": req, "primary": primary, "results": results, "cards": rk["cards"],
                  "eligible_count": rk["eligible_count"], "solution_id": sol_id, "failed_sources": failed_sources}
        return {"reply": text, "state": "done", "solution_id": sol_id, "result": result}

    # ---------- 企业画像 ----------
    def profile(self, name):
        trace = Trace()
        ok, ent = call_tool(trace, "query_enterprise", govdata.query_enterprise, {"name": name}, _s_enterprise)
        if not ok or not ent["found"]:
            return {"found": False, "name": name, "message": "未收录：库内没有名称完全一致的企业", "trace": trace.to_dict()}
        out = {"found": True, "name": name, "enterprise_id": ent["enterprise_id"], "authorized": ent["authorized"], "blocks": {}}
        out["blocks"]["工商"] = _block(ent["facts"], ent["display"])
        if not ent["authorized"]:
            out["message"] = "数据授权状态为「%s」，不读取税务、社保、司法和历史融资。" % (_vals(ent)["data_authorization"] or "空")
            out["trace"] = trace.to_dict()
            return out
        eid = ent["enterprise_id"]
        for label, tname, fn, summ in (("税务", "query_tax", govdata.query_tax, _s_tax), ("社保", "query_social_security", govdata.query_social_security, _s_ss),
                                       ("司法", "query_judicial", govdata.query_judicial, _s_jud)):
            ok, res = call_tool(trace, tname, fn, {"enterprise_id": eid}, summ)
            out["blocks"][label] = _block(res["facts"], res["display"]) if ok else []
        ok, res = call_tool(trace, "query_financing_history", govdata.query_financing_history, {"enterprise_id": eid}, _s_fh)
        if ok:
            blk = _block(res["facts"], {})
            rows = []
            for it in res["items"]:
                rows.append([_entry(f) for f in it.values()])
            out["blocks"]["历史融资"] = blk
            out["history_rows"] = rows
            out["history_count"] = res["count"]
        else:
            out["blocks"]["历史融资"] = []
        out["trace"] = trace.to_dict()
        return out


COL_LABEL = {
    "enterprise_name": "企业名称", "founded_date": "成立日期", "business_status": "经营状态", "region": "区域", "industry": "行业",
    "data_authorization": "数据授权", "is_high_tech": "高新技术企业", "is_specialized": "专精特新", "invention_patent_count": "有效发明专利",
    "enterprise_id": "企业编号", "credit_code": "统一社会信用代码（脱敏）", "legal_representative": "法定代表人（脱敏）",
    "tax_credit_rating": "纳税等级", "taxpayer_type": "纳税人资格", "tax_arrears": "税务欠缴", "tax_paid_12m_wan": "近12个月纳税额（万元）",
    "rating_year": "评价年度", "vat_filing_status": "增值税申报状态", "insured_count": "参保人数", "social_security_arrears": "社保欠缴",
    "latest_payment_month": "最近缴费年月", "is_dishonest": "严重失信", "is_enforced": "被执行", "overdue_unsettled": "逾期未结清",
    "facility_id": "业务编号", "product_name": "历史产品", "status": "状态", "lender": "贷款机构", "disbursed_amount_wan": "发放金额（万元）",
    "guarantee_type": "担保方式", "disbursed_date": "发放日期", "maturity_date": "到期日期",
}
YN_COLS = {"tax_arrears", "social_security_arrears", "is_dishonest", "is_enforced", "overdue_unsettled", "is_high_tech", "is_specialized"}


def _entry(f):
    v = f["value"]
    shown = "未获取" if v is None or v == "" else (("是" if v == 1 else "否") if f["column"] in YN_COLS else v)
    return {"label": COL_LABEL.get(f["column"], f["column"]), "value": shown, "source": "%s · %s.%s" % (f["source"], f["table"], f["column"]),
            "used_in_precheck": f["column"] in config.WHITELIST.get(f["table"], ()), "missing": shown == "未获取"}


def _block(facts, display):
    return [_entry(f) for f in facts.values()] + [_entry(f) for f in display.values()]


AGENT = Agent()
