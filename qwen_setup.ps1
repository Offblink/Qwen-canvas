# 建面板自己的 .venv。面板要 Pillow（造局部编辑的蒙版），aiohttp 可选（拿采样进度）。
#
# 本机 Python 3.13 的 ensurepip 是坏的（安装目录里缺 bundled 的 pip wheel），
# 所以不能用 python -m venv 直接带 pip：先建空 venv，再用基础解释器把 pip 装进 venv。
$ErrorActionPreference = "Stop"
$PanelDir = $PSScriptRoot
$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple"

function Find-BasePython {
    $cands = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$base = Find-BasePython
if (-not $base) { Write-Host "找不到基础 python，请先装 Python 3.12/3.13" -ForegroundColor Red; exit 1 }
$venv = Join-Path $PanelDir ".venv"

if (Test-Path (Join-Path $venv "Scripts\python.exe")) {
    Write-Host "已有 .venv，只补依赖。" -ForegroundColor DarkGray
} else {
    Write-Host "建 .venv (base: $base) ..." -ForegroundColor Cyan
    & $base -m venv --without-pip $venv
    & $base -m pip install -q -i $Mirror --upgrade --target (Join-Path $venv "Lib\site-packages") pip
}
$py = Join-Path $venv "Scripts\python.exe"
Write-Host "装 Pillow / aiohttp ..." -ForegroundColor Cyan
& $py -m pip install -q --disable-pip-version-check -i $Mirror Pillow aiohttp
& $py -c "import PIL, aiohttp; print('Pillow', PIL.__version__, '/ aiohttp', aiohttp.__version__)"
Write-Host "好了。双击 打开画布.cmd 起面板。" -ForegroundColor Green
