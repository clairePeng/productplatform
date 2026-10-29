import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_tmp = tempfile.mkdtemp(prefix="finmatch_test_")
os.environ["FIN_RUNTIME_DIR"] = _tmp
os.environ["FIN_WORK_DB"] = os.path.join(_tmp, "work.sqlite")
os.environ.pop("FIN_FAULT", None)

import pytest  # noqa: E402

from finmatch import db, govdata, products as P  # noqa: E402
from finmatch.agent import AGENT  # noqa: E402


def ingest_samples():
    import glob
    for f in sorted(glob.glob(os.path.join(ROOT, "数据", "产品", "*.pdf"))):
        with open(f, "rb") as fh:
            P.ingest_pdf(fh.read(), os.path.basename(f))


@pytest.fixture()
def fresh():
    os.environ.pop("FIN_FAULT", None)
    govdata.reset_faults()
    db.reset_runtime()
    ingest_samples()
    return AGENT


def names(out):
    return [c["product_name"] for c in out["result"]["cards"]]


def concl(out):
    return {r["product_name"]: r["conclusion"] for r in out["result"]["results"]}


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    # 测试默认不调真实模型；需要模型的用例自己 setenv 并替换传输层
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
