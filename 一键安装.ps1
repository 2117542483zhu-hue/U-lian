# 一键安装：创建独立环境并安装依赖（使用国内镜像，速度更快）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "== 步骤 1/3：检查 Python ==" -ForegroundColor Cyan
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "没有找到 Python。请先安装 Python 3.11 或 3.12，安装时勾选 Add Python to PATH。" -ForegroundColor Red
    Write-Host "下载地址：https://www.python.org/downloads/"
    Read-Host "按回车键退出"
    exit 1
}
python --version

Write-Host "`n== 步骤 2/3：创建虚拟环境并安装依赖 ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

Write-Host "`n== 步骤 3/3：检查姿态模型 ==" -ForegroundColor Cyan
if (-not (Test-Path "模型\pose_landmarker_lite.task")) {
    & ".\.venv\Scripts\python.exe" "工具\下载模型.py"
}
if (Test-Path "模型\pose_landmarker_lite.task") {
    Write-Host "模型就绪。" -ForegroundColor Green
} else {
    Write-Host "模型缺失，请手动下载放到 模型 目录。" -ForegroundColor Yellow
}

Write-Host "`n安装完成。下一步：把视频放进 样例视频 目录，然后运行 一键运行动作自检.ps1" -ForegroundColor Green
Read-Host "按回车键退出"
