param(
    [string]$Password = ""
)

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

if (-not $Password) {
    $Password = Read-Host "请设置手机/其他电脑的访问密码"
}
if (-not $Password) {
    Write-Host "[StockLens] 密码不能为空。" -ForegroundColor Red
    exit 1
}

Write-Host "[StockLens] 手机/其他电脑需与这台电脑在同一局域网。" -ForegroundColor Yellow
Write-Host "[StockLens] 防火墙弹窗请允许 Python 访问专用网络。" -ForegroundColor Yellow
python server.py --host 0.0.0.0 --port 8765 --user stocklens --password $Password --warm --open
