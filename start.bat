@echo off
chcp 65001 >nul
rem Windows 一键启动：建虚拟环境 -> 装依赖 -> 准备 .env -> 启动服务 -> 打开浏览器。PORT 可改端口。
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000

where python >nul 2>nul || (echo 错误：未找到 Python 3.9+，请先安装 https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" || (echo 错误：Python 版本低于 3.9 & pause & exit /b 1)

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import sys" >nul 2>nul || rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\python.exe -c "import fastapi, uvicorn, httpx, pdfplumber, openpyxl, multipart" >nul 2>nul
if errorlevel 1 (
  echo 安装依赖中，离线，来自 vendor\wheels ……
  .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check --no-index --find-links vendor\wheels -r requirements.txt
  if errorlevel 1 (
    echo 无匹配的离线包，改为联网安装……
    .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt || (echo 错误：依赖安装失败 & pause & exit /b 1)
  )
)
if not exist .env copy .env.example .env >nul

rem 后台等服务就绪后入库示例产品并打开浏览器
start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){try{Invoke-RestMethod http://127.0.0.1:$env:PORT/api/health|Out-Null;break}catch{Start-Sleep -Milliseconds 500}}; $p=Invoke-RestMethod http://127.0.0.1:$env:PORT/api/products; if(-not $p -or $p.Count -eq 0){Invoke-RestMethod -Method Post http://127.0.0.1:$env:PORT/api/products/upload-samples|Out-Null}; Start-Process http://127.0.0.1:$env:PORT/"

echo 启动中：http://127.0.0.1:%PORT%/  （Ctrl+C 停止）
.venv\Scripts\python.exe -m uvicorn finmatch.server:app --host 127.0.0.1 --port %PORT%
