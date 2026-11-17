@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [StockLens] 未找到 Python。请先安装 Python 3.10+ 并勾选 Add to PATH。
  pause
  exit /b 1
)

python -c "import requests, numpy" >nul 2>nul
if errorlevel 1 (
  echo [StockLens] 正在安装依赖 requests / numpy ...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [StockLens] 依赖安装失败，请手动运行: python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

echo [StockLens] 启动本地服务 http://127.0.0.1:8765
python server.py --port 8765 --warm --open
pause
