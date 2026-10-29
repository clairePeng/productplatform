"""全局路径与配置加载。所有预审口径来自 config/*.json，改配置不改代码。"""
import json
import os
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
SKILL_DIR = os.path.join(ROOT, "skills")
SEED_DB = os.environ.get("FIN_SEED_DB", os.path.join(ROOT, "数据", "融资数据.sqlite"))
RUNTIME_DIR = os.environ.get("FIN_RUNTIME_DIR", os.path.join(ROOT, "runtime"))
WORK_DB = os.environ.get("FIN_WORK_DB", os.path.join(RUNTIME_DIR, "工作数据.sqlite"))
PRODUCT_PDF_DIR = os.path.join(ROOT, "数据", "产品")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _load(name):
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as f:
        return json.load(f)


RULES = _load("rules.json")
LEXICON = _load("lexicon.json")
BASE_DATE = date.fromisoformat(RULES["base_date"])
RULE_ORDER = [r["id"] for r in RULES["rules"]]
RULE_LABEL = {r["id"]: r["label"] for r in RULES["rules"]}
RULE_SOURCE = {r["id"]: r["source"] for r in RULES["rules"]}
WHITELIST = {t: set(c) for t, c in RULES["whitelist"].items()}
RATING_RANK = {r: len(RULES["rating_order"]) - i for i, r in enumerate(RULES["rating_order"])}
