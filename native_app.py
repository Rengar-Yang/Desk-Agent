#!/usr/bin/env python3
"""Launch Orbit. Desktop entries and installers call this file."""

import os
import sys
import tempfile
import traceback
from pathlib import Path


def _report_startup_error():
    text = traceback.format_exc()
    if os.name != "nt":
        sys.stderr.write(text)
        return
    base = os.environ.get("APPDATA") or tempfile.gettempdir()
    folder = Path(base) / "orbit-codex"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "startup.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        None, "Orbit 启动失败，详情已写入 %APPDATA%\\orbit-codex\\startup.log。\n\n" + text[-900:], "Orbit", 0x10,
    )


if __name__ == "__main__":
    try:
        from orbit.app import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        _report_startup_error()
        raise SystemExit(1)
