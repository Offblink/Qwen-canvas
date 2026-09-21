# Qwen 画布面板（独立项目）：起停**面板自己**，不碰 ComfyUI。
#   打开画布.cmd            -> 起面板 + 开浏览器
#   打开画布.cmd -NoBrowser -> 只起面板
#   qwen_canvas.ps1 -Stop   -> 只停面板
#   qwen_setup.ps1          -> 建/补面板自己的 .venv（Pillow + aiohttp）
#
# ComfyUI 在哪、用哪个 python 起它，写在同目录 canvas.json（或环境变量 QWEN_COMFY_ROOT 等）。
# 点"生成"时面板会按那份配置把 ComfyUI 拉起来；ComfyUI 的停止在模型目录的 停止服务器.cmd。
param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [int]$Port = 8189
)
$ErrorActionPreference = "Stop"
$PanelDir = $PSScriptRoot
$canvasLog = Join-Path $PanelDir "canvas.log"
$canvasUrl = "http://127.0.0.1:$Port"

function Get-PanelProc {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*qwen_canvas.py*" }
}
function Test-Panel {
    try { Invoke-RestMethod -Uri "$canvasUrl/api/state" -TimeoutSec 3 | Out-Null; return $true }
    catch { return $false }
}
function Get-PanelPython {
    $exe = Join-Path $PanelDir ".venv\Scripts\python.exe"
    if (Test-Path $exe) { return $exe }
    Write-Host "面板的 .venv 还没建：先跑 qwen_setup.ps1（面板用自己的 venv，不借 ComfyUI 的）" -ForegroundColor Red
    return $null
}

if ($Stop) {
    $procs = @(Get-PanelProc)
    if ($procs.Count -gt 0) {
        foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Host ("已停止画布面板 (PID " + (($procs | ForEach-Object { $_.ProcessId }) -join ", ") + ")") -ForegroundColor Green
    } else {
        Write-Host "画布面板没在跑。" -ForegroundColor DarkGray
    }
    return
}

$py = Get-PanelPython
if (-not $py) { exit 1 }

if (Test-Panel) {
    Write-Host "画布已在运行: $canvasUrl" -ForegroundColor DarkGray
} else {
    Start-Process -FilePath $py -ArgumentList "qwen_canvas.py" -WorkingDirectory $PanelDir `
        -WindowStyle Hidden -RedirectStandardOutput $canvasLog -RedirectStandardError "$canvasLog.err"
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt 30) {
        if (Test-Panel) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Panel)) {
        Write-Host "面板没起来，看日志: $canvasLog.err" -ForegroundColor Red
        exit 1
    }
    Write-Host "画布面板已启动: $canvasUrl" -ForegroundColor Green
}

Write-Host "配置: $(Join-Path $PanelDir 'canvas.json')" -ForegroundColor DarkGray
Write-Host "画布地址: $canvasUrl   (关掉这个窗口不影响它；停: qwen_canvas.ps1 -Stop)" -ForegroundColor Cyan

if (-not $NoBrowser) { Start-Process $canvasUrl }
