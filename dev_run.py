"""Run Orbit and restart when project source files change. Does not package."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCH_GLOBS = ("*.py", "*.toml")
WATCH_DIRS = (ROOT / "orbit",)
WATCH_FILES = (ROOT / "native_app.py", ROOT / "config.toml")
IGNORE_PARTS = {".git", "__pycache__", "dist", "build", ".venv", "venv"}


def tracked_mtime() -> float:
    latest = 0.0
    for path in WATCH_FILES:
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    for folder in WATCH_DIRS:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORE_PARTS for part in path.parts):
                continue
            if path.suffix and f"*{path.suffix}" not in WATCH_GLOBS and path.name not in {"config.toml"}:
                if path.suffix not in {".py", ".toml"}:
                    continue
            latest = max(latest, path.stat().st_mtime)
    return latest


def start_app() -> subprocess.Popen:
    print("[dev_run] starting native_app.py", flush=True)
    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    env = os.environ.copy()
    env["ORBIT_WATCHER"] = "1"
    return subprocess.Popen(
        [sys.executable, str(ROOT / "native_app.py")],
        cwd=str(ROOT),
        creationflags=creation,
        env=env,
    )


def stop_app(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    print("[dev_run] stopping", flush=True)
    if os.name == "nt":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            proc.wait(timeout=2)
            return
        except Exception:
            pass
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _startup_log() -> str:
    base = os.environ.get("APPDATA") or ""
    path = Path(base) / "orbit-codex" / "startup.log" if base else None
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-1200:]
    except OSError:
        return ""


def main() -> int:
    last = tracked_mtime()
    proc = start_app()
    failures = 0
    try:
        while True:
            time.sleep(0.7)
            current = tracked_mtime()
            exited = proc.poll() is not None
            if current > last + 0.01:
                time.sleep(1.2)
                last = tracked_mtime()
                if proc.poll() is None:
                    print("[dev_run] code changed, restarting…", flush=True)
                    stop_app(proc)
                failures = 0
                proc = start_app()
                continue
            if not exited:
                continue
            code = proc.returncode
            if code in (0, None):
                print("[dev_run] Orbit 已退出，不再自动打开。", flush=True)
                return 0
            failures += 1
            detail = _startup_log().strip()
            if detail:
                print(detail, flush=True)
            if failures >= 3:
                print(f"[dev_run] 连续退出 {failures} 次（最近退出码 {code}），已停止自动重启。", flush=True)
                return code
            delay = failures * 2
            print(f"[dev_run] app exited ({code}), retry in {delay}s…", flush=True)
            time.sleep(delay)
            last = tracked_mtime()
            proc = start_app()
    except KeyboardInterrupt:
        print("\n[dev_run] quit", flush=True)
        stop_app(proc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
