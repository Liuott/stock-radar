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

set "STOCKLENS_PASS="
set /p STOCKLENS_PASS=请设置手机/其他电脑的访问密码（不能为空）:
if "%STOCKLENS_PASS%"=="" (
  echo [StockLens] 密码不能为空。已取消。
  pause
  exit /b 1
)

echo.
echo [StockLens] 手机和其他电脑需要与这台电脑在同一局域网。
echo [StockLens] 如果 Windows 防火墙弹窗，请允许 Python 访问专用网络。
echo [StockLens] 启动后请在控制台查看“局域网/手机”地址。
echo.
python server.py --host 0.0.0.0 --port 8765 --user stocklens --password "%STOCKLENS_PASS%" --warm --open
pause
