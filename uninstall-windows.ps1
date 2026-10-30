# Remove the per-user Orbit install created by install-windows.ps1.
$ErrorActionPreference = "Stop"
$dest = Join-Path $env:LOCALAPPDATA "Programs\Orbit"
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Orbit.lnk"
if (Test-Path $shortcut) {
    Remove-Item $shortcut -Force
}
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $run -Name Orbit -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $run -Name Orbit
}
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Orbit"
if (Test-Path $uninstallKey) {
    Remove-Item $uninstallKey -Recurse -Force
}
if (Test-Path $dest) {
    Remove-Item $dest -Recurse -Force
}
Write-Host "已卸载 Orbit。个人设置仍保留在 $env:APPDATA\orbit-codex"
