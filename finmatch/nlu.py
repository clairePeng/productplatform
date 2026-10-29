"""对话解析：把一句话拆成企业、金额、期限、用途、担保方式、主条件和动作。纯规则，同输入同输出。"""
import re

from . import config

L = config.LEXICON
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_CHARS = "零〇一二两三四五六七八九十百千"
NUM = r"(?:\d[\d,]*(?:\.\d+)?|[%s]+)" % CN_CHARS
SUFFIX = r"(?:集团)?(?:股份有限公司|有限责任公司|有限公司|专业合作社|合作社|公司)|集团"
NAME_CHAR = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()·]")
STOP = sorted(L["name_stop_words"], key=len, reverse=True)

IMPROPER = re.compile(r"伪造|造假|虚假材料|骗贷|套现|洗钱|行贿|贿赂|篡改(?:数据|流水|征信)|绕过(?:审批|风控|授权)|刷流水|黑中介|代办征信")
REGULATION = re.compile(r"法规|法条|条例|监管规定|银保监|金融监管|法律规定|司法解释|国家标准|标准编号")


def cn_to_number(s):
    s = s.strip()
    if not s:
        return None
    if re.fullmatch(r"[\d,]+(?:\.\d+)?", s):
        return float(s.replace(",", ""))
    total, section, cur = 0, 0, None
    for ch in s:
        if ch in CN_DIGIT:
            cur = CN_DIGIT[ch]
        elif ch in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[ch]
            section += (cur if cur is not None else 1) * unit
            cur = None
        else:
            return None
    return float(total + section + (cur or 0))


def split_sentences(text):
    parts = re.split(r"[。！!？?\n；;]+", text)
    return [p.strip() for p in parts if p.strip()]


# ---------------- 企业名称 ----------------

def _left_ok(sentence, idx):
    if idx == 0:
        return True
    prev = sentence[:idx]
    if not NAME_CHAR.match(prev[-1]):
        return True
    return any(prev.endswith(w) for w in STOP)


def find_company(sentence, db_names):
    """返回 ('found', 名称) / ('missing', 候选名称) / None。企业名必须与库内名称完全一致，不做模糊匹配。"""
    best = None
    for name in sorted(db_names, key=len, reverse=True):
        start = 0
        while True:
            i = sentence.find(name, start)
            if i < 0:
                break
            if _left_ok(sentence, i) and (best is None or i < best[0]):
                best = (i, name)
            start = i + 1
    if best:
        return ("found", best[1])
    for m in re.finditer(SUFFIX, sentence):
        j = m.start()
        while j > 0:
            if any(sentence[:j].endswith(w) for w in STOP) or not NAME_CHAR.match(sentence[j - 1]):
                break
            j -= 1
        cand = sentence[j:m.end()]
        if len(cand) - len(m.group(0)) >= 2:
            return ("missing", cand)
    return None


# ---------------- 金额 / 期限 ----------------

AMOUNT_RE = re.compile(r"(?P<num>%s)\s*(?P<unit>亿元?|万元?|元)" % NUM)


def parse_amount(sentence):
    """返回 (金额万元, 问题)。问题：None / 'ambiguous' / 'invalid'。"""
    vals = []
    for m in AMOUNT_RE.finditer(sentence):
        n = cn_to_number(m.group("num"))
        if n is None:
            continue
        u = m.group("unit")
        v = n * 10000 if u.startswith("亿") else (n if u.startswith("万") else n / 10000.0)
        vals.append(v)
    if not vals:
        return None, None
    distinct = sorted(set(vals))
    if len(distinct) > 1 and not re.search(r"改成|改为|调成|调整为|换成|不对|更正|不是", sentence):
        return None, "ambiguous"
    v = vals[-1]
    if v <= 0:
        return None, "invalid"
    return v, None


MONTH_RE = re.compile(r"(?P<num>%s)\s*个月" % NUM)
OTHER_TERM_RE = re.compile(r"(?P<pre>[^，。,；;\s]{0,4}?)(?P<num>%s|半)\s*(?P<unit>天|日|年期?|周)" % NUM)


def parse_term(sentence):
    """期限只接受「个月」。返回 (月数, 问题)。问题：None / 'unit'（只给了天、年等）/ 'invalid'。"""
    months = []
    for m in MONTH_RE.finditer(sentence):
        n = cn_to_number(m.group("num"))
        if n is None or n != int(n) or n <= 0:
            return None, "invalid"
        months.append(int(n))
    if months:
        if len(set(months)) > 1 and not re.search(r"改成|改为|调成|调整为|换成|不对|更正", sentence):
            return None, "ambiguous"
        return months[-1], None
    for m in OTHER_TERM_RE.finditer(sentence):
        if re.search(r"成立|满|已|开业|注册|经营|司龄|成立于", m.group("pre")):
            continue
        return None, "unit"
    if re.search(r"期限\s*(?:是|为|：|:)?\s*\d+(?!\s*(?:万|个月|亿))", sentence):
        return None, "unit"
    return None, None


# ---------------- 用途 / 担保 / 主条件 ----------------

def parse_purpose(sentence, vocab):
    found = []
    terms = {}
    for t in vocab + L["base_purposes"]:
        terms.setdefault(t, t)
    for canon, forms in L["purpose_aliases"].items():
        for f in forms:
            terms.setdefault(f, canon)
    for surface in sorted(terms, key=len, reverse=True):
        if surface in sentence:
            canon = terms[surface]
            if canon not in found:
                found.append(canon)
            sentence = sentence.replace(surface, " ")
    if len(found) == 1:
        return found[0], None
    if len(found) > 1:
        return None, found
    cands = []
    for pat in (r"用途\s*(?:是|为|：|:)?\s*([^，。,；;\s\d]{2,12})",
                r"用于\s*([^，。,；;\s\d]{2,12})",
                r"(?:要|需要|想要)\s*([^\s，。,；;\d]{2,10}?)\s*%s\s*(?:万|亿)" % NUM,
                r"(?:万元?|亿)\s*(?:用于|用来|来)\s*([^\s，。,；;\d]{2,10})"):
        m = re.search(pat, sentence)
        if m:
            c = m.group(1)
            if not re.search(r"个月|产品|预审|确认|放弃|看看|帮我|额度|利率|能不能|可以", c):
                cands.append(c)
    return (cands[0], None) if cands else (None, None)


def parse_guarantee(sentence):
    types = "|".join(L["guarantee_types"])
    if re.search(r"(?:不要|不用|不想|不需要|不接受|别用|别要|无需|免)\s*(?:%s)" % types, sentence):
        return None, "negated"
    pats = [r"(?:担保方式|担保)\s*(?:是|为|按|选|要|：|:)?\s*(?P<g>%s)" % types,
            r"(?:用|走|要|想用|只能|只做|按)\s*(?P<g>%s)(?:方式|担保|贷款)" % types,
            r"(?P<g>%s)(?:方式|担保)" % types,
            r"(?:接受|愿意|同意|选择|选|想用|想要|要求|指定|希望|只要|只能|必须|要|用|走)\s*(?P<g>%s)(?!等级|评级|评分|记录|描述|代码|额度|金)" % types]
    for pat in pats:
        m = re.search(pat, sentence)
        if m:
            pre = sentence[max(0, m.start() - 3):m.start()]
            if re.search(r"不|别|无|免|没", pre) or re.search(r"不(?:要|用|想|做|能)", sentence[max(0, m.start() - 4):m.end()]):
                return None, "negated"
            return m.group("g"), None
    # 单独成句的担保方式，如「12 个月，抵押」
    for clause in re.split(r"[，,、\s]+", sentence):
        m = re.fullmatch(r"(?:要|用|走|按|只做|就)?(%s)(?:的)?(?:就行|吧|即可)?" % types, clause)
        if m:
            return m.group(1), None
    return None, None


def parse_primary(sentence):
    cands = []
    for m in re.finditer(r"(利率|额度)\s*优先", sentence):
        cands.append((m.start(), m.group(1) + "优先", False))
    for m in re.finditer(r"(不|别|莫|无需|不要|不必|别再)\s*(?:按|以|看|用)?\s*(利率|额度)", sentence):
        cands.append((m.start(), m.group(2) + "优先", True))
    for m in re.finditer(r"(?<![不别])(?:按|以|看|用)\s*(利率|额度)\s*(?:排序|排|来排|最低|最低的|最高)", sentence):
        cands.append((m.start(), m.group(1) + "优先", False))
    for m in re.finditer(r"(利率)\s*(?:最低|最小|越低|尽量低|要低|低一点)", sentence):
        cands.append((m.start(), "利率优先", False))
    for m in re.finditer(r"(额度)\s*(?:最高|最大|越高|尽量高|要高|高一点|越大)", sentence):
        cands.append((m.start(), "额度优先", False))
    if not cands:
        return None
    cands.sort()
    pos = [c for c in cands if not c[2]]
    if pos:
        return pos[-1][1]
    neg = cands[-1][1]
    return "额度优先" if neg == "利率优先" else "利率优先"


# ---------------- 区域 / 行业声明 ----------------

def parse_claims(sentence, industries):
    industry_claim, region_claim = None, None
    vocab = sorted(set(L["industry_vocabulary"]) | set(industries), key=len, reverse=True)
    m = re.search(r"行业\s*(?:按|是|为|属于|算|写的是|写|填|选|改成|改为|：|:)?\s*", sentence)
    if m:
        rest = sentence[m.end():]
        hit = next((v for v in vocab if rest.startswith(v)), None)
        if hit:
            industry_claim = hit
        else:
            mm = re.match(r"([^，。,；;\s\d]{2,20})", rest)
            if mm and not re.search(r"优先|产品|不限|预审", mm.group(1)):
                industry_claim = mm.group(1)
    else:
        m2 = re.search(r"属于\s*(%s)" % "|".join(re.escape(v) for v in vocab), sentence)
        if m2:
            industry_claim = m2.group(1)
    m = re.search(r"(?:区域|地区|所在地|所在区域|所属区域)\s*(?:按|是|为|在|属于|改成|改为|：|:)?\s*([^\s，。,；;的\d]{2,6}?[市区县省])", sentence)
    if m:
        region_claim = m.group(1)
    return region_claim, industry_claim


# ---------------- 动作 ----------------

def parse_actions(sentence):
    a = {}
    hold = bool(re.search(r"(?:先别|先不|暂不|不要|别|暂时不|不用|不)\s*(?:急着)?\s*(?:确认|保存)", sentence))
    a["hold"] = hold
    a["confirm"] = bool(re.search(r"确认|保存方案|保存这单|保存预审", sentence)) and not hold
    a["abandon"] = bool(re.search(r"放弃|作废|撤销这单|取消这单", sentence))
    a["gov"] = bool(re.search(r"政府视图|政府看板|政府端", sentence))
    a["report"] = bool(re.search(r"分析报告|报告", sentence))
    a["profile"] = bool(re.search(r"企业画像|画像", sentence))
    a["memory_show"] = bool(re.search(r"(?:查看|看看|显示|列出).{0,4}记忆|记忆.{0,3}(?:有什么|内容)", sentence))
    a["memory_clear"] = bool(re.search(r"(?:清除|清空|删除|忘掉).{0,4}记忆", sentence))
    a["recall"] = bool(re.search(r"(?:上次|刚才|之前|前面).{0,8}(?:结果|结论|方案|预审)", sentence))
    a["same"] = bool(re.search(r"同样|同上|一样|还是这|沿用", sentence))
    return a


def parse_auth(sentence):
    """企业授权答复：'grant' / 'deny' / None。否定说法优先。"""
    if re.search(r"不(?:同意|予|给|愿意|想)?\s*授权|拒绝授权|暂不授权|取消授权|未授权|没有授权|不授权", sentence):
        return "deny"
    if re.search(r"(?:同意|确认|已经|已|完成|通过|获得|取得)\s*(?:了)?\s*授权|授权\s*(?:已)?(?:完成|同意|通过|确认)|已授权|给予授权|我授权", sentence):
        return "grant"
    return None


def parse_sentence(sentence, db_names, purpose_vocab, db_industries):
    comp = find_company(sentence, db_names)
    body = sentence
    if comp:
        body = sentence.replace(comp[1], " ")
    amount, amount_issue = parse_amount(body)
    term, term_issue = parse_term(body)
    purpose, purpose_multi = parse_purpose(body, purpose_vocab)
    guarantee, g_issue = parse_guarantee(body)
    region_claim, industry_claim = parse_claims(body, db_industries)
    d = {
        "text": sentence,
        "company": comp,
        "amount": amount, "amount_issue": amount_issue,
        "term": term, "term_issue": term_issue,
        "purpose": purpose, "purpose_multi": purpose_multi,
        "guarantee": guarantee, "guarantee_issue": g_issue,
        "primary": parse_primary(body),
        "region_claim": region_claim, "industry_claim": industry_claim,
        "improper": bool(IMPROPER.search(sentence)),
        "regulation": bool(REGULATION.search(sentence)),
    }
    d.update(parse_actions(body))
    return d
