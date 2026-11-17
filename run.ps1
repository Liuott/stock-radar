$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[StockLens] 未找到 Python。请安装 Python 3.10+ 并加入 PATH。" -ForegroundColor Red
    exit 1
}

python -c "import requests, numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[StockLens] 正在安装依赖 requests / numpy ..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[StockLens] 依赖安装失败。" -ForegroundColor Red
        exit 1
    }
}

Write-Host "[StockLens] http://127.0.0.1:8765" -ForegroundColor Cyan
python server.py --port 8765 --warm --open
