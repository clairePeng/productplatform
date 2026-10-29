"""上下文管理：多轮记忆、超长压缩、大结果截断、预算裁剪。所有函数不抛异常，溢出时降级而不是崩。

策略
1. 结构化记忆是第一层：企业、已识别槽位、当前方案、授权状态存在 sessions.context，与对话条数无关，永不压缩丢失。
2. 对话原文是第二层：只保留最近 KEEP_MESSAGES 条；更早的由规则压成 ctx["summary"]（企业、槽位、历史需求、方案结论），不靠模型总结，避免压缩时编造。
3. 送给模型的上下文有 token 预算：先丢最旧的原文，再截断摘要，仍超限就不带上下文，模型本身的失败也只会回退规则。
4. 工具大结果（trace、日志、送模型的结果视图）统一截断，截断处标注原长度。
"""
import json
import re

MAX_PROMPT_TOKENS = 1500   # 送模型的会话上下文预算（不含提示词本身）
KEEP_MESSAGES = 8          # 压缩后保留的最近原文条数
COMPACT_AFTER = 16         # 超过这个条数才触发压缩
TOOL_CLIP = 300            # 单个工具参数/返回写入 trace 的最大字符
VIEW_ITEMS = 12            # 送模型的列表最多条数
MSG_CLIP = 120             # 送模型的单条历史最大字符


def est_tokens(s):
    """粗估 token：中文约 0.8/字，其它约 0.25/字符。仅用于预算和接口没返回用量时的兜底。"""
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    cjk = len(re.findall(r"[\u3400-\u9fff]", s))
    return int(cjk * 0.8 + (len(s) - cjk) * 0.25) + 1


def clip(obj, n=TOOL_CLIP):
    """把任意对象压成不超过 n 字符的文本，截断处注明原长。"""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…(已截断，原长 %d 字)" % len(s)


def clip_args(args, n=TOOL_CLIP):
    """trace 里的参数：小的原样保留，大的整体转成截断文本。"""
    try:
        return args if len(json.dumps(args, ensure_ascii=False, default=str)) <= n else clip(args, n)
    except Exception:
        return clip(str(args), n)


def clip_list(items, n=VIEW_ITEMS):
    items = list(items or [])
    if len(items) <= n:
        return items
    return items[:n] + ["…另 %d 项已省略" % (len(items) - n)]


def digest(ctx, older):
    """把被压缩的旧消息 + 结构化状态写成一段摘要（规则生成，不调模型）。"""
    parts = []
    if ctx.get("company"):
        parts.append("企业=%s" % ctx["company"])
    sl = ctx.get("slots") or {}
    named = [("amount", "金额%s万"), ("term", "期限%s个月"), ("purpose", "用途%s"), ("guarantee", "担保%s"), ("primary", "主条件%s")]
    got = [fmt % sl[k] for k, fmt in named if sl.get(k) is not None]
    if got:
        parts.append("当前条件：" + "、".join(got))
    if ctx.get("current_solution"):
        parts.append("当前方案=%s" % ctx["current_solution"])
    users = [m["content"] for m in older if m["role"] == "user"]
    if users:
        parts.append("更早的 %d 次提问（节选）：" % len(users) + "；".join(clip(u, 40) for u in users[-6:]))
    return "。".join(parts)


def maybe_compact(ctx, messages):
    """消息过多时压缩旧消息进 ctx['summary']。原文仍留在库里供页面回看，只是不再进入模型上下文。返回 True 表示压缩过。"""
    try:
        if len(messages) <= COMPACT_AFTER:
            return False
        older = messages[:-KEEP_MESSAGES]
        ctx["summary"] = clip(digest(ctx, older), 600)
        ctx["compacted"] = len(older)
        return True
    except Exception:
        return False


def llm_context(ctx, messages, budget=MAX_PROMPT_TOKENS):
    """生成送模型的会话上下文，保证不超预算。"""
    try:
        summary = ctx.get("summary") or ""
        recent = [{"role": m["role"], "text": clip(m["content"], MSG_CLIP)} for m in messages[-KEEP_MESSAGES:]]
        out = {"summary": summary, "recent": recent}
        while recent and est_tokens(out) > budget:
            recent.pop(0)
        if est_tokens(out) > budget:
            out["summary"] = clip(summary, 200)
        if est_tokens(out) > budget:
            return {}
        return out
    except Exception:
        return {}
