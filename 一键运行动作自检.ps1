# 一键运行：对 样例视频 目录中的视频做动作自检
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "还没有安装环境，请先运行 一键安装.ps1" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

$video = Read-Host "请输入视频文件路径（可直接把文件拖进这个窗口，然后按回车）"
$video = $video.Trim('"').Trim()
if (-not (Test-Path $video)) {
    Write-Host "找不到这个文件：$video" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "`n拍摄角度：侧面拍摄输入 side（推荐），正面拍摄输入 front" -ForegroundColor Cyan
$view = Read-Host "请输入 side 或 front（直接回车默认 side）"
if ([string]::IsNullOrWhiteSpace($view)) { $view = "side" }
if ($view -ne "front") { $view = "side" }

Push-Location "动作自检"
& "..\$python" -X utf8 squat_analyzer.py "$video" --view $view
Pop-Location

Write-Host "`n结果文件在 输出结果 目录：骨架视频、JSON 报告、结论卡片。" -ForegroundColor Green
Read-Host "按回车键退出"
