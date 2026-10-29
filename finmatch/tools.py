"""工具调用封装：超时控制、失败重试、降级，以及 trace 记录。"""
import concurrent.futures
import contextvars
import time
import uuid
from contextlib import contextmanager

from .context import clip, clip_args
from .govdata import AuthorizationError

CURRENT = contextvars.ContextVar("current_trace", default=None)  # 当前请求的 trace，模型层据此记账

_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8)
DEFAULT_TIMEOUT = 8.0
DEFAULT_RETRIES = 2


class Trace:
    """一次对话回合的完整调用链：状态路径、计划、各节点耗时、工具调用、模型调用（token/成本）、出错节点。"""

    def __init__(self, session_id=None):
        self.trace_id = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.t0 = time.time()
        self.tools = []
        self.rules = []
        self.skills = []
        self.notes = []
        self.spans = []
        self.llm = []
        self.flow = []
        self.plan = []
        self.error = None
        self.degraded = []  # 失败但已降级继续的节点
        self.node = None  # 当前所在节点，异常时据此定位

    def rule(self, text):
        if text not in self.rules:
            self.rules.append(text)

    def skill(self, name):
        if name not in self.skills:
            self.skills.append(name)

    def fail(self, node, agent, e):
        if self.error is None:
            self.error = {"node": node, "agent": agent, "type": type(e).__name__, "message": clip(str(e), 200)}

    @contextmanager
    def span(self, node, agent=None):
        """节点计时与出错定位：异常记到具体节点后继续向上抛，由编排层决定降级。"""
        rec = {"node": node, "agent": agent}
        self.spans.append(rec)
        t = time.time()
        prev, self.node = self.node, node
        try:
            yield rec
            rec["status"] = "ok"
        except Exception as e:
            rec["status"] = "error"
            rec["error"] = clip("%s: %s" % (type(e).__name__, e), 200)
            self.fail(node, agent, e)
            raise
        finally:
            rec["ms"] = int((time.time() - t) * 1000)
            self.node = prev

    def usage(self):
        pt = sum(c.get("prompt_tokens", 0) for c in self.llm)
        ct = sum(c.get("completion_tokens", 0) for c in self.llm)
        return {"calls": len(self.llm), "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct,
                "cost_cny": round(sum(c.get("cost_cny", 0) for c in self.llm), 6)}

    def to_dict(self):
        d = {"trace_id": self.trace_id, "total_ms": int((time.time() - self.t0) * 1000),
             "tools": self.tools, "rules": self.rules, "skills": self.skills,
             "flow": self.flow, "plan": self.plan, "spans": self.spans, "llm": self.llm, "usage": self.usage()}
        if self.notes:
            d["notes"] = self.notes
        if self.degraded:
            d["degraded"] = self.degraded
        if self.error:
            d["error"] = self.error
        return d


def call_tool(trace, name, fn, args, summarize=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    """返回 (ok, result_or_error)。授权错误不重试；其它错误和超时按次数重试，仍失败则返回降级信息。"""
    attempts, last = 0, None
    while attempts <= retries:
        attempts += 1
        t0 = time.time()
        fut = _POOL.submit(fn, *args.values())
        try:
            res = fut.result(timeout=timeout)
            entry = {"name": name, "args": clip_args(args), "result_summary": clip(summarize(res) if summarize else "ok")}
            if attempts > 1:
                entry["attempts"] = attempts
            entry["ms"] = int((time.time() - t0) * 1000)
            trace.tools.append(entry)
            return True, res
        except AuthorizationError as e:
            trace.tools.append({"name": name, "args": clip_args(args), "result_summary": clip("拒绝：%s" % e), "error": "authorization"})
            return False, e
        except concurrent.futures.TimeoutError:
            last = "超时（>%ss）" % timeout
            fut.cancel()
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
    trace.tools.append({"name": name, "args": clip_args(args), "result_summary": clip("失败（已重试 %d 次）：%s，该来源按未获取处理" % (retries, last)), "error": "failed", "attempts": attempts})
    trace.degraded.append({"node": name, "reason": clip(last, 120)})
    return False, last
