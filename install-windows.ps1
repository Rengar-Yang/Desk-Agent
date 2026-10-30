# Install Orbit for the current Windows user. Requires 64-bit Python 3.11+.
param(
    [switch]$Autostart
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$root = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA "Programs\Orbit"

if (-not [Environment]::Is64BitProcess) {
    throw "请用 64 位 PowerShell 和 64 位 Python 运行。"
}
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "没有找到 python。请安装 64 位 Python 3.11 或更高版本，并勾选 Add python.exe to PATH。"
}
& python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 版本过低。Orbit 需要 3.11 或更高版本。"
}

Write-Host "安装 PyQt5..."
& python -m pip install "PyQt5>=5.15"
if ($LASTEXITCODE -ne 0) {
    throw "安装 PyQt5 失败。"
}

if ((Resolve-Path $root).Path -eq $dest) {
    throw "请在项目目录运行安装脚本。"
}
New-Item -ItemType Directory -Force -Path $dest | Out-Null
& robocopy $root $dest /E /XD .git dist build __pycache__ .venv /XF *.pyc /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) {
    throw "复制程序失败，robocopy 退出码 $LASTEXITCODE。"
}

$pythonw = Join-Path (Split-Path $python.Source) "pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $pythonw = $python.Source
}
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $startMenu "Orbit.lnk"))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $dest "native_app.py") + '"'
$shortcut.WorkingDirectory = $dest
$shortcut.WindowStyle = 7
$shortcut.Description = "Orbit desktop companion"
$shortcut.Save()

$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Orbit"
New-Item -Path $uninstallKey -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name DisplayName -Value "Orbit" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value "0.2.0" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name Publisher -Value "Orbit" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $dest -PropertyType String -Force | Out-Null
$uninstall = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall-windows.ps1`""
New-ItemProperty -Path $uninstallKey -Name UninstallString -Value $uninstall -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null

if ($Autostart) {
    $run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $command = "`"$pythonw`" `"$dest\native_app.py`""
    New-ItemProperty -Path $run -Name Orbit -Value $command -PropertyType String -Force | Out-Null
}

Write-Host "已安装到 $dest"
Write-Host "可以从开始菜单启动 Orbit。"
