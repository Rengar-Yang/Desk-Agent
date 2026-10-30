"""Login autostart for the current desktop."""

import os
import sys
from pathlib import Path

from orbit.config import CONFIG_DIR, ROOT


def _desktop_file():
    return CONFIG_DIR.parent / "autostart" / "orbit-codex.desktop"


def _launch_command():
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    script = ROOT / "native_app.py"
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = pythonw if pythonw.is_file() else Path(sys.executable)
        return f'"{executable}" "{script}"'
    return f'"{sys.executable}" "{script}"'


def login_autostart_enabled():
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "Orbit")
            return True
        except OSError:
            return False
    return _desktop_file().is_file()


def set_login_autostart(enabled):
    """Enable or remove login autostart. Return an error message, or None."""
    if os.name == "nt":
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, "Orbit", 0, winreg.REG_SZ, _launch_command())
                else:
                    try:
                        winreg.DeleteValue(key, "Orbit")
                    except FileNotFoundError:
                        pass
        except OSError as error:
            return "无法修改 Windows 登录启动项：" + str(error)
        return None
    target = _desktop_file()
    if enabled:
        content = "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            "Name=Orbit Codex",
            "Comment=Desktop companion for local Codex",
            f"Exec={_launch_command()}",
            "Terminal=false",
            "Categories=Utility;Development;",
            "StartupNotify=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ])
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as error:
            return "无法写入登录启动项：" + str(error)
        return None
    try:
        target.unlink(missing_ok=True)
    except OSError as error:
        return "无法关闭登录启动项：" + str(error)
    return None
