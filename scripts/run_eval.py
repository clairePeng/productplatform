"""批量跑测评集，按会话写出 会话记录/<会话>.jsonl。

只读取「题号、会话、承接、问题」四列，不读取「标准结果」列。
用法：
  .venv/bin/python scripts/run_eval.py                       # 公开测评集
  .venv/bin/python scripts/run_eval.py --input 非公开.xlsx   # 现场发放的非公开测评集（可与公开集一起跑）
  .venv/bin/python scripts/run_eval.py --input a.xlsx b.md --no-reset   # 追加到现有工作库
"""
import argparse
import glob
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from finmatch import config, db, memory, products as P  # noqa: E402
from finmatch.agent import AGENT  # noqa: E402

DEFAULT_INPUT = os.path.join(ROOT, "数据", "公开测评集_含标准结果.xlsx")
NEEDED = ["题号", "会话", "承接", "问题"]


def read_questions(path):
    rows = []
    if path.lower().endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True).active
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        idx = {h: header.index(h) for h in NEEDED}
        for r in it:
            if r[idx["题号"]] is None:
                continue
            rows.append({k: ("" if r[idx[k]] is None else str(r[idx[k]]).strip()) for k in NEEDED})
    elif path.lower().endswith(".md"):
        header = None
        for line in open(path, encoding="utf-8"):
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None and "题号" in cells:
                header = cells
                continue
            if header and not set("".join(cells)) <= set("-: "):
                d = dict(zip(header, cells))
                if d.get("题号", "").isdigit():
                    rows.append({k: d.get(k, "") for k in NEEDED})
    else:
        raise SystemExit("不支持的输入格式：" + path)
    return rows


def ingest_all(pdf_dir):
    n = 0
    for f in sorted(glob.glob(os.path.join(pdf_dir, "*.pdf"))):
        with open(f, "rb") as fh:
            p, overwritten = P.ingest_pdf(fh.read(), os.path.basename(f))
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", nargs="*", default=[DEFAULT_INPUT])
    ap.add_argument("--out", default=os.path.join(ROOT, "会话记录"))
    ap.add_argument("--pdf-dir", default=config.PRODUCT_PDF_DIR)
    ap.add_argument("--no-reset", action="store_true", help="不清空工作库，在现有数据上追加")
    ap.add_argument("--no-ingest", action="store_true", help="不重新上传产品 PDF")
    args = ap.parse_args()

    if not args.no_reset:
        db.reset_runtime()
    else:
        db.init_db()
    if not args.no_ingest:
        print("上传并抽取产品 PDF：%d 份" % ingest_all(args.pdf_dir))
    os.makedirs(args.out, exist_ok=True)

    questions = []
    for path in args.input:
        questions.extend(read_questions(path))
    questions.sort(key=lambda q: int(q["题号"]))
    files, t_all = {}, time.time()
    for q in questions:
        sid = q["会话"]
        if not re.fullmatch(r"[A-Za-z]+", sid):
            print("跳过：第 %s 题会话名不是英文字母：%r" % (q["题号"], sid))
            continue
        t0 = time.time()
        out = AGENT.handle(sid, q["问题"])
        ms = int((time.time() - t0) * 1000)
        if sid not in files:
            files[sid] = open(os.path.join(args.out, sid + ".jsonl"), "w", encoding="utf-8", newline="\n")
        fh = files[sid]
        fh.write(json.dumps({"role": "user", "content": q["问题"], "question_no": int(q["题号"])}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"role": "assistant", "content": out["reply"], "trace": out["trace"], "question_no": int(q["题号"]), "elapsed_ms": ms}, ensure_ascii=False) + "\n")
        print("第 %s 题 会话%s 完成（%d ms）" % (q["题号"], sid, ms))
    for fh in files.values():
        fh.close()
    print("共 %d 题，%d 个会话文件，耗时 %.1f 秒 → %s" % (len(questions), len(files), time.time() - t_all, args.out))


if __name__ == "__main__":
    main()
