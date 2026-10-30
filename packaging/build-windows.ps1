# Build dist\Orbit-Setup.exe on 64-bit Windows.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$packaging = $PSScriptRoot
$root = Split-Path -Parent $packaging
Set-Location $root

if (-not [Environment]::Is64BitProcess) {
    throw "请用 64 位 PowerShell 和 64 位 Python 3.11 或更高版本运行此脚本。"
}

Write-Host "生成吉祥物图标..."
python -c "import sys; from pathlib import Path; from PyQt5.QtWidgets import QApplication; from orbit.ui.mascot_icon import write_ico; QApplication(sys.argv); write_ico(Path('packaging') / 'orbit.ico')"

Write-Host "安装打包依赖..."
python -m pip install -r (Join-Path $packaging "requirements-windows.txt")

Write-Host "打包 Orbit..."
python -m PyInstaller (Join-Path $packaging "orbit.spec") --noconfirm --clean
if (-not (Test-Path (Join-Path $root "dist\Orbit\Orbit.exe"))) {
    throw "没有生成 dist\Orbit\Orbit.exe。"
}
# PyInstaller 6 puts data files in _internal. The installer keeps a copy beside
# the exe so an upgrade does not overwrite a config the user already edited.
Copy-Item (Join-Path $root "config.toml") (Join-Path $root "dist\Orbit\config.toml") -Force

function Find-Iscc {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host "未找到 Inno Setup，尝试用 winget 安装..."
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    $iscc = Find-Iscc
}
if (-not $iscc) {
    throw "已生成可直接运行的 dist\Orbit\Orbit.exe，但没有找到 Inno Setup，无法继续生成 Orbit-Setup.exe。请安装 Inno Setup 6 后重新运行。"
}

Write-Host "生成安装包..."
& $iscc (Join-Path $packaging "orbit.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup 编译失败，退出码 $LASTEXITCODE。"
}
Write-Host "安装包已生成：$(Join-Path $root 'dist\Orbit-Setup.exe')"
