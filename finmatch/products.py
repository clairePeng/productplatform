"""产品表：上传时抽取并入库，预审与推荐只读这张表。"""
import csv
import io
from datetime import datetime

from . import db
from .extractor import extract_product, ExtractionError


def now():
    return datetime.now().isoformat(timespec="seconds")


def _row_to_product(r):
    d = dict(r)
    for k in ("purposes", "regions", "industries", "guarantees", "conditions", "extra_conditions", "source_map"):
        d[k] = db.loads(d.get(k), [] if k not in ("conditions", "source_map") else {})
    d["subsidy_supported"] = bool(d["subsidy_supported"])
    return d


def ingest_pdf(data, filename=""):
    """上传一份说明书：抽取成功才写库；同编号覆盖旧记录。返回 (产品, 是否覆盖)。"""
    rec = extract_product(data)
    ts = now()
    with db.connect() as c:
        old = c.execute("SELECT upload_count, uploaded_at FROM products WHERE product_code=?", (rec["product_code"],)).fetchone()
        count = (old["upload_count"] + 1) if old else 1
        first = old["uploaded_at"] if old else ts
        c.execute(
            """INSERT OR REPLACE INTO products
            (product_code, product_name, institution, amount_min, amount_max, rate_min, rate_max, term_min, term_max,
             purposes, regions, industries, guarantees, repay_method, revolving, subsidy_supported,
             conditions, extra_conditions, source_map, intro_text, source_file, file_sha256, upload_count, uploaded_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec["product_code"], rec["product_name"], rec["institution"], rec["amount_min"], rec["amount_max"],
             rec["rate_min"], rec["rate_max"], rec["term_min"], rec["term_max"],
             db.dumps(rec["purposes"]), db.dumps(rec["regions"]), db.dumps(rec["industries"]), db.dumps(rec["guarantees"]),
             rec["repay_method"], rec["revolving"], rec["subsidy_supported"],
             db.dumps(rec["conditions"]), db.dumps(rec["extra_conditions"]), db.dumps(rec["source_map"]),
             rec["intro_text"], filename, rec["file_sha256"], count, first, ts),
        )
    return get_product(rec["product_code"]), bool(old)


def _natkey(code):
    import re
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", code)]


def list_products():
    with db.connect() as c:
        rows = c.execute("SELECT * FROM products").fetchall()
    ps = [_row_to_product(r) for r in rows]
    ps.sort(key=lambda p: _natkey(p["product_code"]))
    return ps


def get_product(code):
    with db.connect() as c:
        r = c.execute("SELECT * FROM products WHERE product_code=?", (code,)).fetchone()
    return _row_to_product(r) if r else None


def delete_product(code):
    with db.connect() as c:
        return c.execute("DELETE FROM products WHERE product_code=?", (code,)).rowcount


def purpose_vocabulary():
    vocab = []
    for p in list_products():
        for t in p["purposes"]:
            if t not in vocab:
                vocab.append(t)
    return vocab


def _rate(x):
    return "%.1f%%" % x if round(x, 1) == x else "%g%%" % x


def product_row(p):
    """导出口径：一行一产品，带数字来源条款。"""
    cond = p["conditions"]
    def val(k, fmt="%s"):
        return fmt % cond[k]["value"] if k in cond else ""
    return {
        "产品编号": p["product_code"],
        "产品名称": p["product_name"],
        "公示机构": p["institution"],
        "额度下限万元": "%g" % p["amount_min"],
        "额度上限万元": "%g" % p["amount_max"],
        "年化利率下限": _rate(p["rate_min"]),
        "年化利率上限": _rate(p["rate_max"]),
        "期限下限月": p["term_min"],
        "期限上限月": p["term_max"],
        "适用用途": "、".join(p["purposes"]),
        "担保方式": "、".join(p["guarantees"]),
        "还款方式": p["repay_method"],
        "是否循环": p["revolving"],
        "制造业贴息": "支持" if p["subsidy_supported"] else "不支持",
        "成立年限要求年": val("founded"),
        "近12个月纳税额下限万元": val("tax_paid", "%g"),
        "纳税等级要求": val("rating"),
        "参保人数下限": val("insured"),
        "区域限制": "、".join(p["regions"]) or "不限",
        "行业限制": "、".join(p["industries"]) or "不限",
        "科技资质要求": "高新技术企业或专精特新或有效发明专利≥1" if "tech" in cond else "否",
        "一般纳税人要求": "是" if cond.get("taxpayer", {}).get("value") == "一般纳税人" else ("否" if "taxpayer" not in cond else cond["taxpayer"]["value"]),
        "未识别条款": "；".join("%s:%s" % (e["name"], e["text"]) for e in p["extra_conditions"]),
        "数字来源条款": "第三条至第六条（第三条用途期限；第四条区域行业；第五条额度利率担保还款；第六条准入）",
        "来源文件": p["source_file"],
        "文件SHA256": p["file_sha256"],
        "上传次数": p["upload_count"],
        "更新时间": p["updated_at"],
    }


def export_csv_text():
    rows = [product_row(p) for p in list_products()]
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
