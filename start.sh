#!/usr/bin/env bash
# 一键启动：建虚拟环境 -> 装依赖 -> 准备 .env（可输入模型密钥）-> 启动服务 -> 入库示例产品 -> 打开浏览器
# 环境变量：PORT 改端口（默认 8000）；NO_BROWSER=1 不开浏览器；PYTHON 指定解释器；
#           DEEPSEEK_API_KEY 直接提供密钥（优先于 .env）；PIP_INDEX_URL 仅在需要联网回退安装时使用
set -e
cd "$(dirname "$0")"
HERE="$(pwd -P)"
PORT="${PORT:-8000}"

# 1) 找 Python 3.9+
PY=""
for c in ${PYTHON:-} python3 python3.12 python3.11 python3.10 python3.9 python; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "错误：未找到 Python 3.9 及以上版本。请先安装 Python（https://www.python.org/downloads/），或用 PYTHON=/路径/python3 ./start.sh 指定。"; exit 1; }

# 2) 端口占用提前报错
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "错误：端口 $PORT 已被占用。换端口：PORT=8001 ./start.sh"; exit 1
fi

# 3) 虚拟环境不能跨机器/跨目录搬运；不可用或路径不符就删掉重建
if [ -d .venv ]; then
  if ! .venv/bin/python -c "import sys" 2>/dev/null || ! grep -qF "$HERE/.venv" .venv/bin/activate 2>/dev/null; then
    echo "检测到 .venv 来自其他机器或目录，已删除并重建"
    rm -rf .venv
  fi
fi
[ -d .venv ] || "$PY" -m venv .venv
if ! .venv/bin/python -c "import fastapi, uvicorn, httpx, pdfplumber, openpyxl, multipart" 2>/dev/null; then
  # 依赖已随工程提供在 vendor/wheels/，优先离线安装；没有匹配本机的安装包才联网
  echo "安装依赖中（离线，来自 vendor/wheels）……"
  if ! .venv/bin/python -m pip install -q --disable-pip-version-check --no-index --find-links vendor/wheels -r requirements.txt 2>/dev/null; then
    echo "本机平台没有匹配的离线包，改为联网安装（需访问 PyPI）……"
    .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt \
      || { echo "错误：依赖安装失败。检查网络，或设置镜像后重试：PIP_INDEX_URL=<镜像地址> ./start.sh"; exit 1; }
  fi
fi

# 4) .env 与模型密钥（不填则用本地规则，系统功能完整可用）
[ -f .env ] || cp .env.example .env
if [ -z "$DEEPSEEK_API_KEY" ] && ! grep -Eq '^DEEPSEEK_API_KEY=.+' .env && [ -t 0 ]; then
  printf "请输入 DeepSeek API Key（deepseek-v4.1-flash；直接回车跳过，使用本地规则）："
  read -r KEY || KEY=""
  if [ -n "$KEY" ]; then
    grep -v '^DEEPSEEK_API_KEY=' .env > .env.tmp || true
    { echo "DEEPSEEK_API_KEY=$KEY"; cat .env.tmp; } > .env
    rm -f .env.tmp
  fi
fi

(
  for _ in $(seq 1 120); do
    curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  if [ "$(curl -s "http://127.0.0.1:$PORT/api/products" | grep -c product_code)" = "0" ]; then
    curl -s -X POST "http://127.0.0.1:$PORT/api/products/upload-samples" >/dev/null
  fi
  echo "就绪：http://127.0.0.1:$PORT/   $(curl -s "http://127.0.0.1:$PORT/api/health")"
  if [ -z "$NO_BROWSER" ]; then
    if command -v open >/dev/null; then open "http://127.0.0.1:$PORT/" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null; then xdg-open "http://127.0.0.1:$PORT/" >/dev/null 2>&1 || true
    fi
  fi
) &

echo "启动中：http://127.0.0.1:$PORT/  （Ctrl+C 停止）"
exec .venv/bin/python -m uvicorn finmatch.server:app --host 127.0.0.1 --port "$PORT"
