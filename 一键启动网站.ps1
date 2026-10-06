# 一键启动网站：装好网页端依赖并启动服务，同时显示手机可访问的地址
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "还没有安装环境，请先运行 一键安装.ps1" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "== 检查网页端依赖 ==" -ForegroundColor Cyan
$hasFastapi = & $python -c "import importlib.util as u; print(bool(u.find_spec('fastapi')))"
if ($hasFastapi -ne "True") {
    & $python -m pip install -r requirements-网页端.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
}

$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*' } |
    Select-Object -ExpandProperty IPAddress

# 8000 被占用时自动换端口，避免启动失败
$port = 8000
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "8000 端口已被占用（可能是上一次没关干净的服务），改用 8123。" -ForegroundColor Yellow
    $port = 8123
}

Write-Host "`n本机打开：http://127.0.0.1:$port" -ForegroundColor Green
foreach ($ip in $ips) {
    Write-Host "同一 WiFi 的手机打开：http://${ip}:$port" -ForegroundColor Green
}
Write-Host "`n按 Ctrl + C 可以停止服务。`n" -ForegroundColor Yellow

Push-Location "网页端"
& "..\$python" -m uvicorn app:app --host 0.0.0.0 --port $port
Pop-Location
