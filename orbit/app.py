"""Start one Orbit instance on an X11 session."""

import os
import sys
from pathlib import Path

from PyQt5.QtCore import QLockFile, QProcess
from PyQt5.QtWidgets import QApplication, QMessageBox

from orbit.config import CONFIG_DIR
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

def main():
    if not os.environ.get("DISPLAY"):
        print("Orbit 当前需要 X11 桌面会话。", file=sys.stderr)
        return 1
    enable_input_method()
    app = QApplication(sys.argv)
    instance_lock = QLockFile(f"/tmp/orbit-codex-{os.getuid()}.lock")
    if not instance_lock.tryLock(0):
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
