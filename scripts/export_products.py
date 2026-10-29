"""导出产品表抽取结果（产品表/产品表抽取结果.csv，utf-8-sig）。先把说明书上传入库，再从产品表导出。
用法：.venv/bin/python scripts/export_products.py [--ingest] [--out 产品表/产品表抽取结果.csv]
"""
import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from finmatch import config, db, products as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", action="store_true", help="先上传数据包内的说明书 PDF")
    ap.add_argument("--out", default=os.path.join(ROOT, "产品表", "产品表抽取结果.csv"))
    args = ap.parse_args()
    db.init_db()
    if args.ingest:
        for f in sorted(glob.glob(os.path.join(config.PRODUCT_PDF_DIR, "*.pdf"))):
            with open(f, "rb") as fh:
                P.ingest_pdf(fh.read(), os.path.basename(f))
    text = P.export_csv_text()
    if not text:
        raise SystemExit("产品表为空，请加 --ingest 先入库")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    print("已导出 %d 个产品 → %s" % (len(P.list_products()), args.out))


if __name__ == "__main__":
    main()
