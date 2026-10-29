#!/usr/bin/env bash
# 打交付包：不含 .venv（不能跨机器）、.env（含密钥）、runtime（本机数据）和缓存。输出 ../融资匹配平台-交付包.zip
set -e
cd "$(dirname "$0")"
NAME="融资匹配平台"
OUT="$(cd .. && pwd)/${NAME}-交付包.zip"
TMP="$(mktemp -d)"
rsync -a --exclude '.venv' --exclude '.env' --exclude 'runtime' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.DS_Store' --exclude '.workbuddy' ./ "$TMP/$NAME/"
rm -f "$OUT"
(cd "$TMP" && zip -qr "$OUT" "$NAME")
rm -rf "$TMP"
echo "已生成：$OUT"
echo "同事使用：解压后 ./start.sh（macOS/Linux）或双击 start.bat（Windows）"
