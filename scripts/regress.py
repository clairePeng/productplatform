"""回归评测：跑 tests/eval_cases.json，逐回合断言，失败时给出调用链编号用于定位。

默认在临时工作库里跑（不碰 runtime/ 下的真实数据），且不调用真实模型（清除 DEEPSEEK_API_KEY）。
用法：
  .venv/bin/python scripts/regress.py              # 全部用例
  .venv/bin/python scripts/regress.py R03 R11      # 只跑指定用例
  .venv/bin/python scripts/regress.py --with-llm   # 带真实模型跑（需要 .env 里的密钥，会产生费用）
退出码：全部通过为 0，否则为 1。
"""
import glob
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

args = [a for a in sys.argv[1:] if not a.startswith("--")]
tmp = tempfile.mkdtemp(prefix="finmatch_regress_")
os.environ["FIN_RUNTIME_DIR"] = tmp
os.environ["FIN_WORK_DB"] = os.path.join(tmp, "work.sqlite")
os.environ.pop("FIN_FAULT", None)

from finmatch import db, llm, products as P, regress  # noqa: E402

if "--with-llm" not in sys.argv:
    os.environ.pop("DEEPSEEK_API_KEY", None)
from finmatch.agent import AGENT  # noqa: E402

db.reset_runtime()
for f in sorted(glob.glob(os.path.join(ROOT, "数据", "产品", "*.pdf"))):
    with open(f, "rb") as fh:
        P.ingest_pdf(fh.read(), os.path.basename(f))

report = regress.run(AGENT, only=set(args) or None)
bad = [r for r in report if not r["passed"]]
for r in report:
    print(("PASS " if r["passed"] else "FAIL ") + r["id"] + " " + r["name"])
    for f in r["failures"]:
        print("   第 %d 句「%s」trace=%s" % (f["turn"], f["say"], f["trace_id"]))
        for e in f["errors"]:
            print("     - " + e)
print("\n%d/%d 通过（模型：%s）" % (len(report) - len(bad), len(report), "真实调用" if llm.enabled() else "未启用"))
sys.exit(1 if bad else 0)
