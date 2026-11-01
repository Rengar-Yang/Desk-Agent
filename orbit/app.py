"""Start one Orbit instance on the current desktop."""

import os
import sys
import tempfile
from pathlib import Path

from PyQt5.QtCore import QLockFile, QProcess
from PyQt5.QtWidgets import QApplication, QMessageBox

from orbit.config import CONFIG_DIR
from orbit.ui.fonts import apply_app_font
from orbit.ui.mascot_icon import app_icon
from orbit.ui.pet import Pet


def enable_input_method():
    """Conda's Qt ships ibus, while this desktop uses fcitx. Load the system fcitx plugin."""
    preferred = os.environ.get("QT_IM_MODULE") or os.environ.get("GTK_IM_MODULE") or ""
    if preferred == "ibus":
        return
    system_plugin = Path("/usr/lib/x86_64-linux-gnu/qt5/plugins/platforminputcontexts/libfcitxplatforminputcontextplugin.so")
    if system_plugin.exists():
        extra = CONFIG_DIR / "qt-plugins"
        folder = extra / "platforminputcontexts"
        folder.mkdir(parents=True, exist_ok=True)
        link = folder / system_plugin.name
        try:
            if link.is_symlink() and link.resolve() != system_plugin.resolve():
                link.unlink()
            if not link.exists():
                link.symlink_to(system_plugin)
        except OSError:
            link = None
        if link is not None and link.exists():
            from PyQt5.QtCore import QCoreApplication
            QCoreApplication.addLibraryPath(str(extra))
            os.environ["QT_IM_MODULE"] = "fcitx"
            os.environ.setdefault("XMODIFIERS", "@im=fcitx")
            return
    os.environ["QT_IM_MODULE"] = "xim"

def lock_path():
    if os.name == "nt":
        return str(Path(tempfile.gettempdir()) / "orbit-codex.lock")
    return f"/tmp/orbit-codex-{os.getuid()}.lock"


def main():
    if os.name == "nt":
        from PyQt5.QtCore import Qt
        from orbit.desktop.win32 import prepare_dpi
        prepare_dpi(Qt, QApplication)
    else:
        if not os.environ.get("DISPLAY"):
            print("Orbit 当前需要 X11 桌面会话。", file=sys.stderr)
            return 1
        enable_input_method()
    app = QApplication(sys.argv)
    apply_app_font(app)
    app.setWindowIcon(app_icon())
    instance_lock = QLockFile(lock_path())
    app._instance_lock = instance_lock
    if not instance_lock.tryLock(0):
        if os.environ.get("ORBIT_WATCHER"):
            print("Orbit 已在运行，不再启动第二个。", flush=True)
            return 0
        QMessageBox.information(None, "Orbit", "Orbit 已在运行。请先退出旧进程，再启动更新后的版本。")
        return 0
    app.setQuitOnLastWindowClosed(False)
    pet = Pet()
    def shutdown():
        pet.bridge.quitting = True
        if pet.bridge.process.state() != QProcess.NotRunning:
            pet.bridge.process.kill()
    app.aboutToQuit.connect(shutdown)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
