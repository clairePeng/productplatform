"""推荐排序：只对「准入符合」排序，名单最多 3 个。数字取自产品表（第五条）。"""
import re

from . import config
from .precheck import CONCLUSION_OK


def _natkey(code):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", code)]


def sort_key(primary, p):
    if primary == "额度优先":
        return (-p["amount_max"], p["rate_max"], _natkey(p["product_code"]))
    return (p["rate_max"], p["amount_max"], _natkey(p["product_code"]))


def _g(x):
    return "%g" % x


def _rate(x):
    return "%.1f" % x if round(x, 1) == x else "%g" % x


def rank(results, products, primary, industry):
    by_code = {p["product_code"]: p for p in products}
    eligible = [by_code[r["product_code"]] for r in results if r["conclusion"] == CONCLUSION_OK]
    eligible.sort(key=lambda p: sort_key(primary, p))
    cards = []
    for i, p in enumerate(eligible[: config.RULES["max_recommend"]], 1):
        subsidy = bool(p["subsidy_supported"] and industry == config.RULES["subsidy_industry"])
        cards.append({
            "rank": i,
            "product_code": p["product_code"],
            "product_name": p["product_name"],
            "amount_range": "%s–%s 万元" % (_g(p["amount_min"]), _g(p["amount_max"])),
            "rate_range": "%s%%–%s%%" % (_rate(p["rate_min"]), _rate(p["rate_max"])),
            "guarantee": "、".join(p["guarantees"]),
            "repay_method": p["repay_method"],
            "term_range": "%s–%s 个月" % (p["term_min"], p["term_max"]),
            "subsidy": subsidy,
            "subsidy_note": config.RULES["subsidy_note"] if subsidy else "",
            "note": config.RULES["rate_note"],
        })
    return {"eligible_count": len(eligible), "eligible_codes": [p["product_code"] for p in eligible], "eligible_names": [p["product_name"] for p in eligible], "cards": cards}
