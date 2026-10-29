"""自测比对：把会话记录与「标准结果」逐题比对。标准结果只在这里读取，用于自测，不进入系统和提示词。

比对内容：主条件、准入符合数、推荐名单（含顺序）、十一份产品各自的结论与原因集合；
政府视图/报告/未收录/材料不一致等无名单的题，比对关键数字与关键词。
用法：.venv/bin/python scripts/compare_gold.py [--xlsx 路径] [--dir 会话记录] [--out 自测结果/公开测评集逐题比对.md]
"""
import argparse
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_detail(text):
    d = {}
    for line in text.split("\n"):
        m = re.match(r"^- (.+?)：(准入符合|准入不符|需人工复核)(?:：(.*))?$", line.strip())
        if m:
            reasons = set(x for x in re.split(r"、", m.group(3) or "") if x)
            d[m.group(1)] = (m.group(2), reasons)
    return d


def parse_list(text):
    return [m.group(1) for m in re.finditer(r"^\d+\. ([^，]+)，额度", text, flags=re.M)]


def parse_head(text):
    m = re.search(r"按(利率优先|额度优先)。(?:暂无合适产品：)?(准入符合 (\d+) 个|无准入符合产品)", text)
    if not m:
        return None, None
    return m.group(1), int(m.group(3)) if m.group(3) else 0


def keyword_check(qno, ours):
    """无名单类题目的关键点。"""
    return [k for k in KW.get(qno, []) if k not in ours]


KW = {
        6: ["未收录"],
        13: ["材料不一致", "软件和信息技术服务业", "制造业"],
        7: ["澄川市 2", "制造业 1", "批发和零售业 1", "税信e贷 1", "日常经营贷 1", "快e周转 1", "1/2"],
        8: ["税信e贷 1", "日常经营贷 1", "快e周转 1", "澄川市 2"],
}


def last_assistant_per_question(d):
    by_q = {}
    for fn in os.listdir(d):
        if not fn.endswith(".jsonl"):
            continue
        for line in open(os.path.join(d, fn), encoding="utf-8"):
            o = json.loads(line)
            if o["role"] == "assistant":
                by_q[o["question_no"]] = o["content"]
    return by_q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=os.path.join(ROOT, "数据", "公开测评集_含标准结果.xlsx"))
    ap.add_argument("--dir", default=os.path.join(ROOT, "会话记录"))
    ap.add_argument("--out", default=os.path.join(ROOT, "自测结果", "公开测评集逐题比对.md"))
    args = ap.parse_args()
    import openpyxl
    ws = openpyxl.load_workbook(args.xlsx, read_only=True).active
    rows = list(ws.iter_rows(values_only=True))[1:]
    ours = last_assistant_per_question(args.dir)
    lines = ["# 公开测评集逐题比对（自测）", "",
             "| 题号 | 会话 | 主条件 | 系统准入符合数 | 系统推荐名单 | 标准推荐名单 | 是否一致 | 差异 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    ok_n = 0
    bad = []
    st = dict(q=0, prim=0, num=0, lst=0, order=0, prod=0, prod_ok=0, reason_ok=0, kw=0, kw_n=0)
    for r in rows:
        qno, sid, gold = int(r[0]), r[1], r[4] or ""
        mine = ours.get(qno, "")
        diffs = []
        g_prim, g_n = parse_head(gold)
        m_prim, m_n = parse_head(mine)
        g_list, m_list = parse_list(gold), parse_list(mine)
        if g_prim:
            st['q'] += 1
            st['prim'] += g_prim == m_prim
            st['num'] += g_n == m_n
            st['lst'] += set(g_list) == set(m_list)
            st['order'] += g_list == m_list
            gd0, md0 = parse_detail(gold), parse_detail(mine)
            for k, v in gd0.items():
                st['prod'] += 1
                st['prod_ok'] += md0.get(k, (None,))[0] == v[0]
                st['reason_ok'] += md0.get(k) == v
            if g_prim != m_prim:
                diffs.append("主条件 %s≠%s" % (m_prim, g_prim))
            if g_n != m_n:
                diffs.append("符合数 %s≠%s" % (m_n, g_n))
            if g_list != m_list:
                diffs.append("名单 %s≠%s" % (m_list, g_list))
            gd, md = parse_detail(gold), parse_detail(mine)
            if set(gd) != set(md):
                diffs.append("产品集合不同")
            for k, v in gd.items():
                if md.get(k) != v:
                    diffs.append("%s：%s≠%s" % (k, md.get(k), v))
        else:
            miss = keyword_check(qno, mine)
            n_kw = len(KW.get(qno, []))
            st['kw_n'] += n_kw
            st['kw'] += n_kw - len(miss)
            if miss:
                diffs.append("缺少关键点：" + "、".join(miss))
        ok = not diffs
        ok_n += ok
        row = "| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            qno, sid, m_prim or "—", m_n if m_n is not None else "—", "／".join(m_list) or "—", "／".join(g_list) or "—",
            "一致" if ok else "不一致", "；".join(diffs) or "—")
        lines.append(row)
        if not ok:
            bad.append(row)
    lines += ["", "**汇总：一致 %d / %d**" % (ok_n, len(rows)), "",
              "| 维度 | 命中 / 总数 |", "| --- | --- |",
              "| 主条件（有名单类题） | %d / %d |" % (st['prim'], st['q']),
              "| 准入符合数 | %d / %d |" % (st['num'], st['q']),
              "| 推荐名单（集合） | %d / %d |" % (st['lst'], st['q']),
              "| 推荐名单（含顺序） | %d / %d |" % (st['order'], st['q']),
              "| 产品级结论（符合／不符／复核） | %d / %d |" % (st['prod_ok'], st['prod']),
              "| 产品级结论＋原因集合 | %d / %d |" % (st['reason_ok'], st['prod']),
              "| 无名单题关键点 | %d / %d |" % (st['kw'], st['kw_n'])]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("汇总：一致 %d / %d" % (ok_n, len(rows)))
    for b in bad:
        print(b)


if __name__ == "__main__":
    main()
