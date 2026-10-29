"""Skill 注册表：每个 skills/*.json 描述一个可复用工作流（触发条件、输入输出契约、入口）。"""
import importlib
import json
import os

from . import config

REQUIRED = ("id", "name", "trigger", "input", "output", "entry")


def load_all():
    out = {}
    for fn in sorted(os.listdir(config.SKILL_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(config.SKILL_DIR, fn), encoding="utf-8") as f:
            d = json.load(f)
        missing = [k for k in REQUIRED if k not in d]
        if missing:
            raise ValueError("skill %s 缺少字段 %s" % (fn, missing))
        out[d["id"]] = d
    return out


SKILLS = load_all()


def name(skill_id):
    return SKILLS[skill_id]["name"]


def check_entries():
    """校验每个 skill 的入口都能导入，返回问题列表。"""
    bad = []
    for sid, d in SKILLS.items():
        mod, _, attr = d["entry"].rpartition(".")
        try:
            getattr(importlib.import_module(mod), attr)
        except Exception as e:
            bad.append((sid, str(e)))
    return bad
