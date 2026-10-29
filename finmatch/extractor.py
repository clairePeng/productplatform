"""产品说明书 PDF 抽取。只读取第三条至第六条的表格，导语、附章、第十条至第十六条不参与数字抽取。

做法：按页面坐标把每张表格归到其上方最近的「第X条」标题之下，再按条款解析。
"""
import hashlib
import io
import re

from . import config

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
HEADING_RE = re.compile(r"^(第([一二三四五六七八九十百]+)条|附章|导语)")
SEP = r"\s*(?:至|到|~|～|-|—|–)\s*"


class ExtractionError(Exception):
    def __init__(self, message, missing=None):
        super().__init__(message)
        self.message = message
        self.missing = missing or []


def cn_to_int(s):
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + CN_NUM.get(s[1:], 0)
    if s.endswith("十"):
        return CN_NUM.get(s[0], 0) * 10
    if len(s) == 3 and s[1] == "十":
        return CN_NUM.get(s[0], 0) * 10 + CN_NUM.get(s[2], 0)
    return CN_NUM.get(s, 0)


def _clean(cell):
    if cell is None:
        return ""
    return re.sub(r"\s*\n\s*", "", str(cell)).strip()


def _split_list(text):
    parts = re.split(r"[、，,；;/／]|\s+和\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _layout(pdf):
    """返回 (标题列表, 表格列表)，表格带所属条款号。"""
    items = []
    for pi, page in enumerate(pdf.pages):
        for ln in page.extract_text_lines():
            m = HEADING_RE.match(ln["text"].strip())
            if m:
                label = m.group(1)
                num = cn_to_int(m.group(2)) if m.group(2) else (0 if label == "导语" else 99)
                items.append((pi, ln["top"], "h", {"label": ln["text"].strip(), "num": num}))
        for t in page.find_tables():
            rows = [[_clean(c) for c in r] for r in t.extract()]
            items.append((pi, t.bbox[1], "t", rows))
    items.sort(key=lambda x: (x[0], x[1]))
    clause = {"num": -1, "label": "首页"}
    tables, heads, intro_lines = [], [], []
    for pi, top, kind, payload in items:
        if kind == "h":
            clause = payload
            heads.append(payload)
        else:
            tables.append({"clause": clause["num"], "label": clause["label"], "rows": payload})
    return heads, tables, pdf


def _kv(tables, clause_num):
    d = {}
    for t in tables:
        if t["clause"] != clause_num:
            continue
        for r in t["rows"]:
            if len(r) >= 2 and r[0] not in ("项目", "序号"):
                d.setdefault(r[0], r[1])
    return d


def _intro(pdf):
    text = []
    on = False
    for page in pdf.pages:
        for ln in page.extract_text_lines():
            t = ln["text"].strip()
            if t == "导语":
                on = True
                continue
            if on and re.match(r"^第[一二三四五六七八九十百]+条", t):
                return "".join(text)
            if on:
                text.append(t)
    return "".join(text)


def extract_product(data):
    """从 PDF 字节流抽取一条产品记录。失败抛 ExtractionError，不写任何库。"""
    import pdfplumber

    if not data or data[:5] != b"%PDF-":
        raise ExtractionError("文件不是有效的 PDF（缺少 PDF 文件头），已拒绝入库。")
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
        heads, tables, pdf = _layout(pdf)
        intro = _intro(pdf)
    except Exception as e:  # 损坏文件、加密文件等
        raise ExtractionError("PDF 无法解析（文件损坏或受保护）：%s" % type(e).__name__)
    if not tables:
        raise ExtractionError("PDF 中没有识别到任何表格，无法抽取产品要素。")

    head = _kv(tables, -1)
    c3, c4, c5 = _kv(tables, 3), _kv(tables, 4), _kv(tables, 5)
    cond_rows = []
    for t in tables:
        if t["clause"] == 6:
            for r in t["rows"]:
                if len(r) >= 3 and re.fullmatch(r"\d+", r[0]):
                    cond_rows.append((r[1], r[2]))

    smap, missing = {}, []

    def need(cond, field, clause, item, text):
        if cond:
            smap[field] = {"clause": clause, "item": item, "text": text}
        else:
            missing.append("%s（%s·%s）" % (field, clause, item))

    rec = {
        "product_code": head.get("产品编号", ""),
        "product_name": head.get("产品名称", ""),
        "institution": head.get("公示机构", ""),
        "intro_text": intro,
    }
    if not rec["product_code"]:
        missing.append("产品编号（首页）")
    if not rec["product_name"]:
        missing.append("产品名称（首页）")

    t = c5.get("额度", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万元?%s(\d+(?:\.\d+)?)\s*万元?" % SEP, t)
    rec["amount_min"], rec["amount_max"] = (float(m.group(1)), float(m.group(2))) if m else (None, None)
    need(m, "额度区间", "第五条", "额度", t)

    t = c5.get("年化利率", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*%%%s(\d+(?:\.\d+)?)\s*%%" % SEP, t)
    rec["rate_min"], rec["rate_max"] = (float(m.group(1)), float(m.group(2))) if m else (None, None)
    need(m, "年化利率区间", "第五条", "年化利率", t)

    t = c3.get("期限", "")
    m = re.search(r"(\d+)\s*个月%s(\d+)\s*个月" % SEP, t)
    rec["term_min"], rec["term_max"] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    need(m, "期限区间", "第三条", "期限", t)

    t = c3.get("适用用途", "")
    rec["purposes"] = _split_list(t)
    need(rec["purposes"], "适用用途", "第三条", "适用用途", t)

    for key, item in (("regions", "适用区域"), ("industries", "适用行业")):
        t = c4.get(item, "")
        vals = _split_list(t)
        # 写明「不限」时不因该项否决，用空列表表示
        rec[key] = [] if (vals and all(v == "不限" for v in vals)) else vals
        need(bool(vals), item, "第四条", item, t)

    t = c5.get("担保方式", "")
    rec["guarantees"] = _split_list(t)
    need(rec["guarantees"], "担保方式", "第五条", "担保方式", t)

    t = c5.get("还款方式", "")
    rec["repay_method"] = t
    need(t, "还款方式", "第五条", "还款方式", t)
    rec["revolving"] = c5.get("是否循环", "")
    if rec["revolving"]:
        smap["是否循环"] = {"clause": "第五条", "item": "是否循环", "text": rec["revolving"]}
    t = c5.get("制造业贴息", "")
    rec["subsidy_supported"] = 1 if t.startswith("支持") else 0
    if t:
        smap["制造业贴息"] = {"clause": "第五条", "item": "制造业贴息", "text": t}

    conditions, extra = {}, []
    names = config.RULES["condition_names"]
    for name, text in cond_rows:
        cid = names.get(name)
        if not cid:
            extra.append({"name": name, "text": text})
            continue
        param = {"text": text}
        if cid == "rating":
            m = re.search(r"不低于\s*([ABMC])", text)
            param["value"] = m.group(1) if m else None
        elif cid == "insured":
            m = re.search(r"不少于\s*(\d+)\s*人", text)
            param["value"] = int(m.group(1)) if m else None
        elif cid == "founded":
            m = re.search(r"满\s*(\d+)\s*年", text)
            param["value"] = int(m.group(1)) if m else None
        elif cid == "tax_paid":
            m = re.search(r"不少于\s*(\d+(?:\.\d+)?)\s*万元", text)
            param["value"] = float(m.group(1)) if m else None
        elif cid == "taxpayer":
            m = re.search(r"为\s*(\S*?纳税人)", text)
            param["value"] = m.group(1) if m else None
        elif cid == "tech":
            param["value"] = True
        else:
            param["value"] = True
        if param["value"] is None:
            extra.append({"name": name, "text": text})
            continue
        conditions[cid] = param
        smap["条件:" + name] = {"clause": "第六条", "item": name, "text": text}
    for need_id, label in (("rating", "纳税等级"), ("insured", "参保人数"), ("founded", "成立年限"), ("tax_paid", "纳税额")):
        if need_id not in conditions:
            missing.append("准入条件·%s（第六条）" % label)

    if missing:
        raise ExtractionError("抽取失败，缺少要素：" + "、".join(missing), missing)
    rec["conditions"] = conditions
    rec["extra_conditions"] = extra
    rec["source_map"] = smap
    rec["file_sha256"] = hashlib.sha256(data).hexdigest()
    return rec
