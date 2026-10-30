#!/usr/bin/env python3
"""Orbit desktop companion for X11, backed by the local Codex app-server."""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import random
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt5.QtCore import QAbstractAnimation, QObject, QPoint, QProcess, QProcessEnvironment, QRect, Qt, QTimer, QEasingCurve, QPropertyAnimation, pyqtSignal, QLockFile
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QCursor, QRegion
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizeGrip, QSizePolicy,
    QVBoxLayout, QWidget,
)


APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "orbit-codex"
CONFIG_FILE = CONFIG_DIR / "settings.json"


def load_api_token():
    for path in (APP_DIR / "api.txt", CONFIG_DIR / "api.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        token = next((line.strip() for line in lines if line.strip()), "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            return token
    return ""


def find_codex():
    found = shutil.which("codex")
    if found:
        return found
    candidates = sorted((Path.home() / ".nvm" / "versions" / "node").glob("*/bin/codex"))
    return str(candidates[-1]) if candidates else None


def run_x(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=0.7)
    except (OSError, subprocess.SubprocessError):
        return ""


SKIPPED_WINDOW_TYPES = (
    "_NET_WM_WINDOW_TYPE_DESKTOP",
    "_NET_WM_WINDOW_TYPE_DOCK",
    "_NET_WM_WINDOW_TYPE_SPLASH",
)
ACCENTS = {
    "files": "#7869d9",
    "editor": "#6684dc",
    "browser": "#d8966f",
    "terminal": "#2f9d86",
    "other": "#8370da",
}
KIND_LABELS = {
    "files": "文件管理器",
    "editor": "代码编辑器",
    "browser": "浏览器",
    "terminal": "终端",
    "other": "窗口",
}
CONTEXT_PROMPTS = {
    "files": ["这个文件夹里有什么？", "帮我整理这些文件", "找出需要处理的文件"],
    "editor": ["解释当前文件", "检查可能的问题", "基于这个文件继续改"],
    "browser": ["总结当前页面", "提炼三个要点", "我下一步该看什么？"],
    "terminal": ["当前目录在做什么？", "解释正在运行的命令", "下一步该运行什么？"],
    "other": ["这个窗口是做什么的？", "给我下一步建议", "帮我处理手头的事"],
    None: ["聊聊今天的计划", "给我一个灵感", "你能做什么？"],
}
SECRET_NAME = re.compile(r"(?i)(^api\.txt$|(^|\.)env($|\.)|credential|secret|token|password|id_rsa|\.pem$|cookies)")
SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".cache"}
SHELLS = {"bash", "zsh", "fish", "sh", "dash"}
PLACE_NAMES = {
    "home": "home", "主文件夹": "home", "主目录": "home",
    "desktop": "desktop", "桌面": "desktop",
    "documents": "documents", "文档": "documents",
    "downloads": "downloads", "下载": "downloads",
    "music": "music", "音乐": "music",
    "pictures": "pictures", "图片": "pictures",
    "videos": "videos", "视频": "videos", "movies": "videos",
}


def query_windows(own_ids, only_id=None):
    """Return managed X11 windows in bottom-to-top order. Rects are tuples."""
    raw = run_x("xprop", "-root", "_NET_CLIENT_LIST_STACKING")
    ids = re.findall(r"0x[0-9a-fA-F]+", raw)
    if only_id:
        ids = [wid for wid in ids if wid == only_id]
    current_desktop = desktop_number(run_x("xprop", "-root", "_NET_CURRENT_DESKTOP"))
    result = []
    for wid in ids:
        if int(wid, 16) in own_ids:
            continue
        props = run_x("xprop", "-id", wid, "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME", "WM_NAME",
                      "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE", "_NET_WM_DESKTOP",
                      "_GTK_FRAME_EXTENTS", "_NET_FRAME_EXTENTS")
        if any(kind in props for kind in SKIPPED_WINDOW_TYPES):
            continue
        if "_NET_WM_STATE_HIDDEN" in props:
            continue
        window_desktop = desktop_number(props)
        if current_desktop is not None and window_desktop not in (None, current_desktop, 0xFFFFFFFF):
            continue
        pid_match = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", props)
        pid = int(pid_match.group(1)) if pid_match else None
        if pid == os.getpid():
            continue
        command = b""
        if pid:
            try:
                command = (Path("/proc") / str(pid) / "cmdline").read_bytes()
                if any(Path(os.fsdecode(arg)).name in ("native_app.py", "orbit-codex") for arg in command.split(b"\0") if arg):
                    continue
            except OSError:
                pass
        geo = run_x("xwininfo", "-id", wid)
        if "Map State: IsViewable" not in geo:
            continue
        values = {}
        for key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
            match = re.search(rf"{re.escape(key)}:\s*(-?\d+)", geo)
            if match:
                values[key] = int(match.group(1))
        if len(values) != 4 or values["Width"] < 80 or values["Height"] < 60:
            continue
        title_match = re.search(r'_NET_WM_NAME\([^\n]*? = "((?:\\.|[^"\\\n])*)"', props)
        if not title_match:
            title_match = re.search(r'WM_NAME\([^\n]*? = "((?:\\.|[^"\\\n])*)"', props)
        class_match = re.search(r'WM_CLASS\([^\n]*? = (.+)', props)
        title = unescape_xprop(title_match.group(1)) if title_match else "窗口"
        if title.startswith("@!"):
            continue
        app_class = class_match.group(1).replace('"', "") if class_match else "应用"
        cwd = None
        if pid:
            try:
                cwd = str((Path("/proc") / str(pid) / "cwd").resolve(strict=True))
            except OSError:
                pass
        result.append({
            "id": wid, "title": title, "app": app_class, "pid": pid, "cwd": cwd,
            "rect": visible_window_rect(values, props),
            "bounds": outer_window_rect(values, props),
            "kind": classify_window(app_class),
        })
    return result


def desktop_number(text):
    match = re.search(r"_NET_(?:CURRENT|WM)_DESKTOP\(CARDINAL\) = (\d+)", text or "")
    return int(match.group(1)) if match else None


def outer_window_rect(values, props):
    """Full window including decorations. This is the layer the user can see."""
    x, y = values["Absolute upper-left X"], values["Absolute upper-left Y"]
    width, height = values["Width"], values["Height"]
    match = re.search(r"_NET_FRAME_EXTENTS\(CARDINAL\)\s*=\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", props)
    if match:
        left, right, top, bottom = map(int, match.groups())
        return (x - left, y - top, width + left + right, height + top + bottom)
    return (x, y, width, height)


def topmost_at(windows, point):
    """Pick the uppermost visible window under a point. Lower layers stay hidden."""
    for window in reversed(windows):
        area = window.get("bounds") or window["rect"]
        if area.contains(point):
            return window
    return None


def visible_window_rect(values, props):
    """Exclude GTK client shadows; include server-drawn window decorations."""
    x, y = values["Absolute upper-left X"], values["Absolute upper-left Y"]
    width, height = values["Width"], values["Height"]
    for name, sign in (("_GTK_FRAME_EXTENTS", 1), ("_NET_FRAME_EXTENTS", -1)):
        match = re.search(rf"{name}\(CARDINAL\)\s*=\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", props)
        if match:
            left, right, top, bottom = map(int, match.groups())
            candidate = (x + sign * left, y + sign * top, width - sign * (left + right), height - sign * (top + bottom))
            if candidate[2] > 40 and candidate[3] > 40:
                return candidate
    return (x, y, width, height)


def query_window_rect(wid):
    """Visible rect of one window, or None when it is gone or hidden."""
    geo = run_x("xwininfo", "-id", wid)
    if "Map State: IsViewable" not in geo:
        return None
    values = {}
    for key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
        match = re.search(rf"{re.escape(key)}:\s*(-?\d+)", geo)
        if match:
            values[key] = int(match.group(1))
    if len(values) != 4 or values["Width"] < 80 or values["Height"] < 60:
        return None
    props = run_x("xprop", "-id", wid, "_GTK_FRAME_EXTENTS", "_NET_FRAME_EXTENTS", "_NET_WM_STATE")
    if "_NET_WM_STATE_HIDDEN" in props:
        return None
    return visible_window_rect(values, props)


def unescape_xprop(text):
    if "\\" not in text:
        return text
    try:
        return text.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeError, ValueError):
        return text


def classify_window(app_class):
    app = app_class.lower()
    if any(name in app for name in ("nautilus", "nemo", "thunar", "dolphin", "caja")):
        return "files"
    if any(name in app for name in ("terminal", "kitty", "alacritty", "wezterm", "konsole", "ptyxis")):
        return "terminal"
    if any(name in app for name in ("firefox", "chrome", "chromium", "brave", "msedge")):
        return "browser"
    if any(name in app for name in ("cursor", "code", "gedit", "kate", "sublime", "emacs", "texteditor")):
        return "editor"
    return "other"


def hydrate_window(window):
    hydrated = dict(window)
    hydrated["rect"] = QRect(*window["rect"])
    if window.get("bounds"):
        hydrated["bounds"] = QRect(*window["bounds"])
    return hydrated


def looks_secret(path):
    return bool(SECRET_NAME.search(Path(path).name))


def read_text_preview(path, limit=6000):
    file_path = Path(path)
    if looks_secret(file_path) or not file_path.is_file():
        return None
    try:
        if file_path.stat().st_size > 1_000_000:
            return None
        data = file_path.read_bytes()[:limit]
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def list_directory(path, limit=60):
    try:
        entries = list(os.scandir(path))
    except OSError:
        return []
    entries.sort(key=lambda entry: (not entry.is_dir(follow_symlinks=False), entry.name.lower()))
    visible = []
    for entry in entries:
        if entry.name.startswith(".") or entry.name in SKIP_DIRS:
            continue
        visible.append(entry.name + ("/" if entry.is_dir(follow_symlinks=False) else ""))
        if len(visible) >= limit:
            break
    return visible


def xdg_places():
    places = {"home": Path.home()}
    defaults = {
        "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
        "music": "Music", "pictures": "Pictures", "videos": "Videos",
    }
    for key, name in defaults.items():
        places[key] = Path.home() / name
    config = Path.home() / ".config" / "user-dirs.dirs"
    try:
        for line in config.read_text().splitlines():
            match = re.match(r'XDG_([A-Z]+)_DIR="([^"]+)"', line)
            if match:
                places[match.group(1).lower()] = Path(os.path.expandvars(match.group(2))).expanduser()
    except OSError:
        pass
    return places


def resolve_path_buttons(names):
    if not names:
        return None
    places = xdg_places()
    first = names[0].strip()
    place = PLACE_NAMES.get(first.lower())
    if place == "home" or first in ("/", "Computer", "计算机"):
        current = Path("/") if first in ("/", "Computer", "计算机") else places["home"]
        names = names[1:]
    elif place:
        current = places.get(place, Path.home() / first)
        names = names[1:]
    else:
        current = None
        for root in (places["home"], Path("/")):
            candidate = root / first
            if candidate.is_dir():
                current = candidate
                names = names[1:]
                break
    if current is None or not Path(current).is_dir():
        return None
    for name in names:
        nxt = Path(current) / name
        if not nxt.is_dir():
            return None
        current = nxt
    return Path(current)


def parse_terminal_cwd(title):
    match = re.match(r"^[^@\s]+@[^:]+:\s*(.+)$", title.strip())
    if not match:
        return None
    raw = match.group(1).strip()
    if not (raw.startswith("~") or raw.startswith("/")):
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def parse_editor_title(title):
    cleaned = re.sub(r"\s+[—–-]\s+(Cursor|Visual Studio Code|Code|VSCodium|Text Editor|gedit|Kate)$", "", title).strip()
    located = re.search(r"\((~?[/~][^)]+)\)\s*$", cleaned)
    path = Path(located.group(1)).expanduser() if located and Path(located.group(1)).expanduser().exists() else None
    parts = [part.strip() for part in re.split(r"\s+[—–-]\s+", cleaned) if part.strip()]
    filename = parts[0] if parts else None
    workspace = parts[1] if len(parts) > 1 else None
    if filename and "/" in filename:
        filename = Path(filename).name
    return filename, workspace, path


def file_uri_to_path(uri):
    if not uri:
        return None
    if uri.startswith("file:"):
        return Path(unquote(urlparse(uri).path))
    return Path(uri)


def code_workspace_folders(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    folders = []
    for item in data.get("folders", []):
        folder = item.get("path")
        if not folder:
            continue
        candidate = Path(folder)
        if not candidate.is_absolute():
            candidate = Path(path).parent / candidate
        if candidate.is_dir():
            folders.append(candidate)
    return folders


def editor_roots():
    roots = []
    bases = [
        Path.home() / ".config" / "Cursor" / "User" / "workspaceStorage",
        Path.home() / ".config" / "Code" / "User" / "workspaceStorage",
        Path.home() / ".config" / "Code - OSS" / "User" / "workspaceStorage",
    ]
    for base in bases:
        if not base.is_dir():
            continue
        for workspace in base.glob("*/workspace.json"):
            try:
                data = json.loads(workspace.read_text())
            except (OSError, ValueError):
                continue
            folder = file_uri_to_path(data.get("folder"))
            if folder and folder.is_dir():
                roots.append(folder)
            file_path = file_uri_to_path(data.get("workspace"))
            if file_path:
                roots.extend(code_workspace_folders(file_path))
    return roots


def find_named_file(root, filename):
    hits = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).relative_to(root).parts) > 6:
            dirnames.clear()
            continue
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
        if filename in filenames:
            hits.append(Path(dirpath) / filename)
            if len(hits) >= 4:
                break
    if not hits:
        return None
    hits.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return hits[0]


def locate_editor_file(title):
    filename, workspace, explicit = parse_editor_title(title)
    if explicit and explicit.is_file():
        return explicit
    if not filename:
        return None
    ranked = []
    for root in editor_roots():
        score = 0
        if workspace and root.name.lower() == workspace.lower():
            score += 5
        elif workspace and (workspace.lower() in root.name.lower() or root.name.lower() in workspace.lower()):
            score += 2
        found = find_named_file(root, filename)
        if found:
            score += 3
            ranked.append((score, found.stat().st_mtime, found))
    if ranked:
        ranked.sort(reverse=True)
        return ranked[0][2]
    return None


def process_table():
    table = {}
    children = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text()
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        end = stat.rfind(")")
        fields = stat[end + 2:].split()
        if len(fields) < 2:
            continue
        ppid = int(fields[1])
        comm = stat[stat.find("(") + 1:end]
        table[pid] = {"ppid": ppid, "comm": comm, "cmd": command, "cwd": cwd}
        children.setdefault(ppid, []).append(pid)
    return table, children


def descendant_shell_cwd(pid):
    if not pid:
        return None
    table, children = process_table()
    stack = list(children.get(pid, []))
    cwds = []
    seen = set()
    while stack:
        current = stack.pop()
        if current in seen or current not in table:
            continue
        seen.add(current)
        stack.extend(children.get(current, []))
        if table[current]["comm"] in SHELLS and table[current]["cwd"]:
            cwds.append(table[current]["cwd"])
    unique = set(cwds)
    if len(unique) == 1:
        path = Path(next(iter(unique)))
        return path if path.is_dir() else None
    return None


def foreground_commands(root_pid, cwd, limit=4):
    """Commands started from shells that belong to this window, not every process in the directory."""
    if not root_pid:
        return []
    table, children = process_table()
    target = str(cwd)
    shells = []
    seen = set()
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        info = table[pid]
        if info["comm"] in SHELLS and info["cwd"] == target:
            shells.append(pid)
        stack.extend(children.get(pid, []))
    lines = []
    for shell in shells:
        for pid in children.get(shell, []):
            info = table.get(pid)
            if not info or info["cwd"] != target or not info["cmd"]:
                continue
            text = re.sub(r"\s+", " ", info["cmd"])[:160]
            if text not in lines:
                lines.append(text)
            if len(lines) >= limit:
                return lines
    return lines


def dbus_result(connection, destination, path, interface, method, signature="", body=()):
    from jeepney import DBusAddress, MessageType, new_method_call
    message = new_method_call(DBusAddress(path, destination, interface), method, signature, body)
    reply = connection.send_and_get_reply(message, timeout=1.2)
    if reply.header.message_type == MessageType.error:
        raise RuntimeError(reply.body[0] if reply.body else "dbus error")
    return reply.body[0] if reply.body else None


def a11y_connection():
    from jeepney.io.blocking import open_dbus_connection
    session = open_dbus_connection(bus="SESSION")
    try:
        address = dbus_result(session, "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress")
    finally:
        session.close()
    return open_dbus_connection(bus=address.split(",guid=")[0])


def a11y_properties(connection, destination, path):
    data = dbus_result(connection, destination, path, "org.freedesktop.DBus.Properties", "GetAll", "s", ("org.a11y.atspi.Accessible",))
    return {key: (value[1] if isinstance(value, tuple) and len(value) == 2 else value) for key, value in data.items()}


def accessibility_snapshot(pid, title, rect):
    """Read the path bar, visible names, and address text exposed by the window."""
    if not pid:
        return {}
    try:
        connection = a11y_connection()
    except Exception:
        return {}
    try:
        names = dbus_result(connection, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
        destination = None
        for name in names:
            if not str(name).startswith(":"):
                continue
            try:
                owner = dbus_result(connection, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (name,))
            except Exception:
                continue
            if int(owner) == int(pid):
                destination = name
                break
        if not destination:
            return {}
        frames = dbus_result(connection, destination, "/org/a11y/atspi/accessible/root", "org.a11y.atspi.Accessible", "GetChildren")
        best = None
        best_distance = None
        for child_dest, child_path in frames:
            props = a11y_properties(connection, child_dest, child_path)
            role = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Accessible", "GetRoleName")
            if role not in ("frame", "window") or props.get("Name") != title:
                continue
            distance = 0
            try:
                extents = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Component", "GetExtents", "u", (0,))
                cx, cy = extents[0] + extents[2] / 2, extents[1] + extents[3] / 2
                distance = abs(cx - (rect[0] + rect[2] / 2)) + abs(cy - (rect[1] + rect[3] / 2))
            except Exception:
                distance = 0
            if best_distance is None or distance < best_distance:
                best = (child_dest, child_path)
                best_distance = distance
        if not best:
            return {}
        return walk_accessible(connection, *best)
    except Exception:
        return {}
    finally:
        connection.close()


def walk_accessible(connection, destination, root_path):
    path_buttons = []
    files = []
    selection = None
    blobs = []
    stack = [(destination, root_path, 0)]
    seen = 0
    while stack and seen < 420:
        dest, path, depth = stack.pop()
        seen += 1
        try:
            props = a11y_properties(connection, dest, path)
            role = dbus_result(connection, dest, path, "org.a11y.atspi.Accessible", "GetRoleName")
            children = dbus_result(connection, dest, path, "org.a11y.atspi.Accessible", "GetChildren") if props.get("ChildCount") else []
        except Exception:
            continue
        names = []
        child_nodes = []
        for child_dest, child_path in children:
            try:
                child_props = a11y_properties(connection, child_dest, child_path)
                child_role = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Accessible", "GetRoleName")
            except Exception:
                continue
            if child_role in ("menu", "menu item", "check menu item", "separator"):
                continue
            child_name = child_props.get("Name") or ""
            names.append((child_role, child_name))
            child_nodes.append((child_dest, child_path, child_role, child_name))
            if depth < 11:
                stack.append((child_dest, child_path, depth + 1))
        buttons = path_from_names(names)
        if len(buttons) > len(path_buttons):
            path_buttons = buttons
        segments = path_bar_segments(connection, child_nodes)
        if len(segments) > len(path_buttons):
            path_buttons = segments
        name = props.get("Name") or ""
        if role == "canvas" and name:
            files.append(name)
        if role in ("entry", "document", "link") and name:
            blobs.append(name)
        selected = re.search(r"[“\"](.+?)[”\"] selected|[“\"](.+?)[””]已选", name)
        if selected:
            selection = next(group for group in selected.groups() if group)
    url = next((item for item in blobs if item.startswith(("http://", "https://"))), None)
    return {"buttons": path_buttons, "files": files[:80], "selection": selection, "url": url}


def named_button(connection, destination, path):
    try:
        props = a11y_properties(connection, destination, path)
        if not props.get("ChildCount"):
            return None
        children = dbus_result(connection, destination, path, "org.a11y.atspi.Accessible", "GetChildren")
    except Exception:
        return None
    found = None
    for child_dest, child_path in children:
        try:
            child_props = a11y_properties(connection, child_dest, child_path)
            child_role = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Accessible", "GetRoleName")
        except Exception:
            continue
        child_name = child_props.get("Name") or ""
        if child_role == "push button" and child_name and child_name.lower() != "menu":
            found = child_name
    return found


def path_bar_segments(connection, child_nodes):
    """Read a path bar whose crumbs are sibling fillers, as in Nautilus."""
    if sum(1 for node in child_nodes if node[2] == "filler") < 2:
        return []
    segments = []
    started = False
    for dest, path, role, _name in child_nodes:
        if role != "filler":
            if started:
                break
            continue
        button = named_button(connection, dest, path)
        if not button:
            if started:
                break
            continue
        started = True
        segments.append(button)
    return segments if len(segments) >= 2 else []


def path_from_names(names):
    best = []
    for index, (role, name) in enumerate(names):
        if role != "push button" or not name or name.lower() == "menu":
            continue
        buttons = [name]
        cursor = index + 1
        while cursor + 1 < len(names) and names[cursor] == ("label", "/") and names[cursor + 1][0] == "push button" and names[cursor + 1][1]:
            buttons.append(names[cursor + 1][1])
            cursor += 2
        if len(buttons) > len(best):
            best = buttons
    if len(best) >= 2 or (len(best) == 1 and best[0].lower() in PLACE_NAMES):
        return best
    return []


def unique_directory_named(name):
    if not name or len(name) > 180 or "/" in name:
        return None
    home = Path.home()
    matches = []
    for dirpath, dirnames, _filenames in os.walk(home):
        relative = Path(dirpath).relative_to(home)
        if len(relative.parts) > 4:
            dirnames.clear()
            continue
        dirnames[:] = [item for item in dirnames if not item.startswith(".") and item not in SKIP_DIRS]
        if name in dirnames:
            matches.append(Path(dirpath) / name)
            if len(matches) > 1:
                return None
    return matches[0] if matches else None


def capture_context(window):
    """Attach a path, listing, and bounded text preview to a window description."""
    kind = window.get("kind") or classify_window(window.get("app", ""))
    title = window.get("title") or "窗口"
    rect = window.get("rect") or (0, 0, 0, 0)
    if isinstance(rect, QRect):
        rect = (rect.x(), rect.y(), rect.width(), rect.height())
    snapshot = accessibility_snapshot(window.get("pid"), title, rect) if kind in ("files", "browser", "editor") else {}
    path = None
    entries = []
    preview = None
    detail = ""
    if kind == "files":
        path = resolve_path_buttons(snapshot.get("buttons") or [])
        titled = unique_directory_named(title)
        if titled and (path is None or (path != titled and path in titled.parents)):
            path = titled
        if path:
            entries = list_directory(path)
            selected = snapshot.get("selection")
            selected_path = path / selected if selected and selected not in (path.name, title) else None
            if selected_path and selected_path.is_file():
                preview = read_text_preview(selected_path, 4000)
                detail = f"当前选中 {selected}"
            elif entries:
                detail = f"{len(entries)} 个可见项目"
    elif kind == "terminal":
        path = parse_terminal_cwd(title) or descendant_shell_cwd(window.get("pid"))
        if path:
            entries = foreground_commands(window.get("pid"), path)
            if entries:
                names = []
                for command in entries[:3]:
                    program = command.split()[0]
                    names.append(Path(program).name)
                detail = "正在运行 " + "、".join(names)
            else:
                detail = "停在命令提示符"
    elif kind == "editor":
        file_path = locate_editor_file(title)
        if file_path:
            path = file_path
            preview = read_text_preview(file_path)
            detail = "已读取文件开头" if preview else "文件不是文本或体积过大"
    elif kind == "browser":
        detail = snapshot.get("url") or "未暴露页面地址，可以依据标题协助"
        path = None
    if path is None and kind == "other":
        cwd = window.get("cwd")
        if cwd and Path(cwd) not in (Path.home(), Path("/")) and Path(cwd).is_dir():
            path = Path(cwd)
            entries = list_directory(path)
    summary = " · ".join(part for part in (str(path) if path else "", detail) if part)
    return {
        "kind": kind,
        "accent": ACCENTS[kind],
        "path": str(path) if path else None,
        "entries": entries,
        "preview": preview,
        "summary": summary or KIND_LABELS[kind],
        "url": snapshot.get("url"),
    }


def context_prompt(window, cwd):
    if not window:
        return ""
    kind = KIND_LABELS.get(window.get("kind"), "窗口")
    lines = [
        "你正在协助用户操作一个已经吸附的 Linux 窗口。请直接使用下面的路径和内容，不要再向用户索取路径或要求粘贴文件。",
        f"窗口类型：{kind}",
        f"窗口标题：{window.get('title', '')}",
        f"程序：{window.get('app', '')}",
    ]
    target = window.get("path")
    if target:
        lines.append(f"目标路径：{target}")
        if cwd and os.path.normpath(cwd) != os.path.normpath(target):
            lines.append(f"可写目录：{cwd}")
        lines.append("只根据目标路径和下面的目录内容回答，不要改用用户主目录。")
    elif cwd and Path(cwd) != Path.home():
        lines.append(f"工作目录：{cwd}")
    else:
        lines.append("还没有解析出这个窗口的路径。不要把用户主目录当作当前窗口。")
    if window.get("url"):
        lines.append(f"页面地址：{window['url']}")
    entries = window.get("entries") or []
    if entries and window.get("kind") == "files":
        lines.append("目录内容：\n" + "\n".join(f"- {entry}" for entry in entries[:60]))
    elif entries and window.get("kind") == "terminal":
        lines.append("该目录中的活动命令：\n" + "\n".join(f"- {entry}" for entry in entries[:8]))
    preview = window.get("preview")
    if preview:
        lines.append("文本内容开头：\n" + preview[:6000])
    elif window.get("kind") == "browser" and not window.get("url"):
        lines.append("浏览器没有暴露页面正文。若已知地址，请用工具抓取页面，不要只根据标题编造正文。")
    return "\n".join(lines) + "\n"


API_TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "列出当前工作目录中的文件和子目录。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对当前目录的路径，省略表示当前目录本身"},
        }},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取当前工作目录里的文本文件。敏感文件会被拒绝。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对或绝对路径，必须位于当前工作目录内"},
            "offset": {"type": "integer", "description": "从第几行开始，默认 1"},
            "limit": {"type": "integer", "description": "最多读取多少行，默认 200，最大 400"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "search",
        "description": "在当前工作目录中搜索纯文本。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "description": "相对子目录，可省略"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "把文件中唯一出现的一段原文替换成新文本。用户确认后才会写入。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "要替换的原文，必须完整且只出现一次"},
            "new": {"type": "string", "description": "替换后的文本"},
        }, "required": ["path", "old", "new"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "新建或覆盖当前工作目录中的文本文件。用户确认后才会写入。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "shell",
        "description": "在当前工作目录运行一条 shell 命令。用户确认后才会执行。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "抓取 http 或 https 页面并返回可见文本，用于浏览器窗口没有正文时。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
]


def confirm_action(title, detail, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Orbit 确认")
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
    dialog.resize(480, 380)
    layout = QVBoxLayout(dialog)
    heading = QLabel(title)
    heading.setWordWrap(True)
    layout.addWidget(heading)
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setPlainText(detail[:12000])
    layout.addWidget(text, 1)
    buttons = QHBoxLayout()
    deny = QPushButton("拒绝")
    allow = QPushButton("允许")
    deny.clicked.connect(dialog.reject)
    allow.clicked.connect(dialog.accept)
    buttons.addStretch()
    buttons.addWidget(deny)
    buttons.addWidget(allow)
    layout.addLayout(buttons)
    return dialog.exec_() == QDialog.Accepted


def approval_summary(params):
    lines = []
    if params.get("command"):
        lines.append("命令：" + str(params["command"]))
    if params.get("cwd"):
        lines.append("目录：" + str(params["cwd"]))
    if params.get("reason"):
        lines.append("原因：" + str(params["reason"]))
    if params.get("grantRoot"):
        lines.append("写入范围：" + str(params["grantRoot"]))
    if params.get("permissions"):
        lines.append("权限：" + json.dumps(params["permissions"], ensure_ascii=False)[:2000])
    if not lines:
        lines.append(json.dumps(params, ensure_ascii=False)[:2000])
    return "\n".join(lines)


def scoped_path(root, raw):
    base = Path(root).resolve()
    candidate = Path(str(raw or ".")).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError("路径超出当前目录：" + str(base))
    return resolved


def clip_text(text, limit=12000):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…已截断"


def read_file_text(path, offset=1, limit=200):
    if looks_secret(path):
        return "敏感文件，已拒绝读取：" + path.name
    try:
        offset = max(1, int(offset or 1))
        limit = min(400, max(1, int(limit or 200)))
    except (TypeError, ValueError):
        offset, limit = 1, 200
    try:
        if path.stat().st_size > 1_000_000:
            return "文件超过 1MB，请缩小读取范围或改用搜索。"
        with path.open("rb") as handle:
            sample = handle.read(800)
        if b"\0" in sample:
            return "这不是文本文件。"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        return "读取失败：" + str(error)
    chosen = lines[offset - 1:offset - 1 + limit]
    body = "\n".join(f"{offset + index}|{line}" for index, line in enumerate(chosen))
    return clip_text(f"{path} 共 {len(lines)} 行，显示 {offset}–{offset + len(chosen) - 1 if chosen else offset - 1}\n{body}")


def search_files(root, query, limit=40):
    hits = []
    if shutil.which("rg"):
        try:
            completed = subprocess.run(
                ["rg", "-n", "-F", "--max-count", str(limit), "-g", "!**/.git/**", "-g", "!**/node_modules/**", query, str(root)],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return "搜索失败：" + str(error)
        lines = []
        for line in completed.stdout.splitlines():
            if looks_secret(line.split(":", 1)[0]):
                continue
            lines.append(line)
        return clip_text("\n".join(lines[:limit]) or "没有匹配。")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [item for item in dirnames if not item.startswith(".") and item not in SKIP_DIRS]
        for name in filenames:
            if name.startswith(".") or looks_secret(Path(name)):
                continue
            file_path = Path(dirpath) / name
            try:
                if file_path.stat().st_size > 1_000_000:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query in line:
                    hits.append(f"{file_path.relative_to(root)}:{number}:{line[:200]}")
                    if len(hits) >= limit:
                        return clip_text("\n".join(hits))
    return clip_text("\n".join(hits) or "没有匹配。")


def html_to_text(raw):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def fetch_page(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "只允许抓取 http 或 https 地址。"
    request = urllib.request.Request(url, headers={"User-Agent": "Orbit/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(150_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception as error:
        return "抓取失败：" + api_error_text(error)
    return clip_text(html_to_text(payload.decode(charset, "replace")), 8000)


def run_tool(name, args, root, confirm):
    args = args or {}
    try:
        if name == "list_dir":
            path = scoped_path(root, args.get("path") or ".")
            if not path.is_dir():
                return "不是目录：" + str(path)
            entries = list_directory(path, 200)
            return clip_text(str(path) + "\n" + "\n".join(entries) or "空目录")
        if name == "read_file":
            return read_file_text(scoped_path(root, args.get("path")), args.get("offset"), args.get("limit"))
        if name == "search":
            query = str(args.get("query") or "")
            if not query:
                return "缺少搜索词。"
            return search_files(scoped_path(root, args.get("path") or "."), query)
        if name == "edit_file":
            path = scoped_path(root, args.get("path"))
            old, new = str(args.get("old") or ""), str(args.get("new") if args.get("new") is not None else "")
            if not path.is_file():
                return "文件不存在：" + str(path)
            if looks_secret(path):
                return "敏感文件，已拒绝修改：" + path.name
            original = path.read_text(encoding="utf-8", errors="replace")
            count = original.count(old) if old else 0
            if not old or count != 1:
                return f"原文需要唯一匹配，当前出现 {count} 次。"
            preview = f"{path}\n\n--- 原文 ---\n{old[:4000]}\n\n--- 改为 ---\n{new[:4000]}"
            if not confirm(f"修改 {path.name}", preview):
                return "用户拒绝了这次修改。"
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            return "已写入 " + str(path)
        if name == "write_file":
            path = scoped_path(root, args.get("path"))
            if looks_secret(path):
                return "敏感文件，已拒绝写入：" + path.name
            content = str(args.get("content") if args.get("content") is not None else "")
            if len(content) > 200_000:
                return "内容过长，已拒绝写入。"
            action = "覆盖" if path.exists() else "新建"
            if not confirm(f"{action} {path.name}", f"{path}\n\n{content[:8000]}"):
                return "用户拒绝了这次写入。"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return "已写入 " + str(path)
        if name == "shell":
            command = str(args.get("command") or "").strip()
            if not command:
                return "缺少命令。"
            if not confirm("运行命令", f"目录：{root}\n\n{command}"):
                return "用户拒绝了这条命令。"
            try:
                completed = subprocess.run(command, shell=True, cwd=str(root), capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                return "命令超过 30 秒，已中止。"
            except OSError as error:
                return "命令失败：" + str(error)
            output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
            return clip_text(f"退出码 {completed.returncode}\n{output}".strip())
        if name == "fetch_url":
            return fetch_page(str(args.get("url") or ""))
        return "未知工具：" + name
    except (OSError, ValueError) as error:
        return str(error)


def tool_label(name, args):
    args = args or {}
    target = args.get("path") or args.get("url") or args.get("command") or args.get("query") or ""
    names = {
        "list_dir": "列出目录", "read_file": "读取文件", "search": "搜索",
        "edit_file": "修改文件", "write_file": "写入文件", "shell": "运行命令", "fetch_url": "抓取网页",
    }
    text = names.get(name, name)
    return text + ((" · " + str(target)[:80]) if target else "")


class WindowProbe(QObject):
    """Poll X11 off the UI thread so the mascot animation keeps its frame time."""

    found = pyqtSignal(int, object)
    placed = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._busy = False
        self._again = None
        self.seq = 0
        self._watch = None
        self._watch_running = False

    def request(self, own_ids):
        with self._lock:
            self.seq += 1
            job = (self.seq, set(own_ids))
            if self._busy:
                self._again = job
                return
            self._busy = True
        threading.Thread(target=self._work, args=job, daemon=True).start()

    def watch(self, wid):
        with self._lock:
            self._watch = str(wid)
            if self._watch_running:
                return
            self._watch_running = True
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def unwatch(self):
        with self._lock:
            self._watch = None

    def _watch_loop(self):
        last = None
        seen = None
        miss_sent = False
        while True:
            with self._lock:
                wid = self._watch
                if wid is None:
                    if self._watch is None:
                        self._watch_running = False
                        return
                    wid = self._watch
            if wid != seen:
                seen = wid
                last = None
                miss_sent = False
            try:
                rect = query_window_rect(wid)
            except Exception:
                rect = None
            if rect is None:
                if not miss_sent:
                    miss_sent = True
                    last = None
                    self.placed.emit(wid, None)
            else:
                miss_sent = False
                if rect != last:
                    last = rect
                    self.placed.emit(wid, rect)
            time.sleep(0.04)

    def _work(self, seq, own_ids):
        try:
            windows = query_windows(own_ids)
        except Exception:
            windows = []
        self.found.emit(seq, windows)
        with self._lock:
            again = self._again
            self._again = None
            if again is None:
                self._busy = False
                return
        threading.Thread(target=self._work, args=again, daemon=True).start()


class ContextBridge(QObject):
    ready = pyqtSignal(int, str, object)


class CodexBridge(QWidget):
    status = pyqtSignal(str)
    delta = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.on_exit)
        self.process.errorOccurred.connect(self.process_error)
        self.buffer = b""
        self.next_id = 1
        self.callbacks = {}
        self.ready = False
        self.thread_id = None
        self.thread_cwd = None
        self.pending = None
        self.active_turn = None
        self.answer = ""
        self.stderr_tail = ""
        self.generation = 0
        self.answer_generation = 0
        self.silent_exit = False
        self.quitting = False
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self.timed_out)
        self.error.connect(lambda _: self.watchdog.stop())
        self.finished.connect(lambda _: self.watchdog.stop())

    def timed_out(self):
        self.stop()
        self.pending = None
        self.error.emit("Codex 长时间未返回进度，已请求停止。请检查网络、登录状态或重试。")

    def send(self, method, params=None, callback=None):
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        if callback:
            self.callbacks[request_id] = callback
        self.process.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())

    def notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.process.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())

    def start(self):
        if self.process.state() != QProcess.NotRunning:
            return
        codex = find_codex()
        if not codex:
            self.pending = None
            self.error.emit("找不到本机 codex 命令。请先安装并登录 Codex CLI。")
            return
        self.status.emit("正在连接本机 Codex…")
        self.process.setProgram(codex)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PATH", str(Path(codex).parent) + os.pathsep + environment.value("PATH"))
        self.process.setProcessEnvironment(environment)
        self.stderr_tail = ""
        self.buffer = b""
        self.callbacks.clear()
        self.process.setArguments(["app-server", "--listen", "stdio://"])
        self.process.setWorkingDirectory(str(Path.home()))
        self.process.start()
        self.send("initialize", {"clientInfo": {"name": "orbit_desktop", "title": "Orbit Desktop", "version": "0.1.0"}}, self.initialized)

    def initialized(self, message):
        if "error" in message:
            self.pending = None
            self.ready = False
            self.silent_exit = True
            self.error.emit(str(message["error"].get("message", "初始化失败")))
            self.process.kill()
            return
        self.notify("initialized")
        self.ready = True
        self.status.emit("Codex 已连接")
        self.flush_pending()

    def invalidate(self):
        self.generation += 1
        self.stop()
        self.active_turn = None
        self.thread_id = None
        self.thread_cwd = None
        self.pending = None
        self.answer = ""
        self.watchdog.stop()

    def ask(self, text, cwd, context):
        if self.active_turn:
            self.error.emit("上一条消息仍在处理中，可先点击停止。")
            return
        self.generation += 1
        self.answer_generation = self.generation
        self.pending = (text, cwd, context)
        self.watchdog.start(120000)
        if not self.ready:
            self.start()
        else:
            self.flush_pending()

    def flush_pending(self):
        if not self.ready or not self.pending:
            return
        text, cwd, context = self.pending
        if self.thread_id and self.thread_cwd == cwd:
            self.pending = None
            self.start_turn(text, cwd, context)
            return
        self.thread_id = None
        self.thread_cwd = cwd
        self.status.emit("正在创建 Codex 会话…")
        self.send("thread/start", {"cwd": cwd, "sandbox": "workspace-write", "approvalPolicy": "on-request"}, self.thread_started)

    def thread_started(self, message):
        if "error" in message:
            self.pending = None
            self.error.emit(str(message["error"].get("message", "创建会话失败")))
            return
        self.thread_id = message.get("result", {}).get("thread", {}).get("id")
        if not self.thread_id:
            self.pending = None
            self.error.emit("Codex 未返回会话 ID")
            return
        if self.pending:
            text, cwd, context = self.pending
            self.pending = None
            self.start_turn(text, cwd, context)

    def start_turn(self, text, cwd, context):
        prefix = context_prompt(context, cwd)
        if prefix:
            text = prefix + "用户请求：" + text
        self.answer = ""
        self.status.emit("Codex 正在思考…")
        self.send("turn/start", {"threadId": self.thread_id,
                                  "input": [{"type": "text", "text": text}],
                                  "cwd": cwd,
                                  "approvalPolicy": "on-request",
                                  "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": True}},
                  self.turn_started)

    def turn_started(self, message):
        if "error" in message:
            self.error.emit(str(message["error"].get("message", "发送失败")))
            return
        self.active_turn = message.get("result", {}).get("turn", {}).get("id")

    def stop(self):
        if self.active_turn and self.thread_id:
            self.send("turn/interrupt", {"threadId": self.thread_id, "turnId": self.active_turn})

    def read_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                self.handle(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                continue

    def handle(self, message):
        if "id" in message and ("result" in message or "error" in message):
            callback = self.callbacks.pop(message["id"], None)
            if callback:
                callback(message)
            return
        method = message.get("method", "")
        params = message.get("params", {})
        if self.watchdog.isActive():
            self.watchdog.start(120000)
        if "id" in message:
            self.reply_to_server(message)
            return
        if self.answer_generation != self.generation:
            return
        turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
        if self.active_turn and turn_id and turn_id != self.active_turn:
            return
        if method == "item/agentMessage/delta":
            self.status.emit("Codex 正在回复…")
            piece = params.get("delta", "")
            self.answer += piece
            self.delta.emit(piece)
        elif method == "item/started":
            kind = params.get("item", {}).get("type", "")
            if kind in ("commandExecution", "fileChange", "mcpToolCall"):
                self.status.emit("Codex 正在执行…")
        elif method == "error":
            if not params.get("willRetry", False):
                self.active_turn = None
                self.error.emit((params.get("error") or {}).get("message", "Codex 请求失败"))
            else:
                self.status.emit("Codex 正在重试连接…")
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage" and not self.answer:
                text = item.get("text", "")
                if text:
                    self.answer = text
                    self.delta.emit(text)
        elif method == "turn/completed":
            turn = params.get("turn", {})
            state = turn.get("status", "failed")
            self.active_turn = None
            if state == "completed":
                self.finished.emit(self.answer or "（Codex 没有返回文字）")
                self.status.emit("Codex 已就绪")
            elif state == "interrupted":
                self.finished.emit(self.answer or "已停止")
                self.status.emit("已停止")
            else:
                detail = (turn.get("error") or {}).get("message", "Codex 执行失败")
                self.error.emit(detail)

    def reply_to_server(self, message):
        method = message.get("method", "")
        params = message.get("params") or {}
        if method == "currentTime/read":
            result = {"currentTimeAt": int(time.time())}
        elif method == "item/permissions/requestApproval":
            detail = approval_summary(params)
            if confirm_action("Codex 请求额外权限", detail, self):
                result = {"permissions": params.get("permissions") or {}, "scope": "turn"}
                self.status.emit("已允许 Codex 的权限请求")
            else:
                payload = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": "用户拒绝了权限请求"}}
                self.process.write((json.dumps(payload) + "\n").encode())
                self.status.emit("已拒绝 Codex 的权限请求")
                return
        elif method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            title = "Codex 请求执行命令" if "command" in method else "Codex 请求修改文件"
            accepted = confirm_action(title, approval_summary(params), self)
            result = {"decision": "accept" if accepted else "decline"}
            self.status.emit("已允许 Codex 操作" if accepted else "已拒绝 Codex 操作")
        elif method in ("execCommandApproval", "applyPatchApproval"):
            title = "Codex 请求执行命令" if method == "execCommandApproval" else "Codex 请求修改文件"
            accepted = confirm_action(title, approval_summary(params), self)
            result = {"decision": "approved" if accepted else "abort"}
            self.status.emit("已允许 Codex 操作" if accepted else "已拒绝 Codex 操作")
        else:
            payload = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": "Orbit declined this request"}}
            self.process.write((json.dumps(payload) + "\n").encode())
            self.status.emit("Codex 请求了本版本无法展示的操作，已拒绝")
            return
        payload = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        self.process.write((json.dumps(payload) + "\n").encode())

    def read_error(self):
        self.stderr_tail = (self.stderr_tail + bytes(self.process.readAllStandardError()).decode(errors="replace"))[-2000:]

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.pending = None
            self.silent_exit = True
            self.error.emit("无法启动 Codex：" + self.process.errorString())

    def on_exit(self, code=0, status=None):
        self.read_error()
        self.ready = False
        self.thread_id = None
        self.active_turn = None
        self.pending = None
        self.callbacks.clear()
        if self.silent_exit or self.quitting:
            self.silent_exit = False
            return
        detail = self.stderr_tail.strip()
        if "sqlite state runtime" in detail:
            detail = "Codex 无法写入状态数据库。请检查 CODEX_HOME（默认 ~/.codex）的写权限，以及启动程序是否处于只读沙箱中。\n" + detail
        self.error.emit(f"Codex 进程已退出（退出码 {code}）。\n{detail or '未返回错误详情，请检查 codex CLI 登录和配置。'}")


class SettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Orbit 设置")
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.provider = QComboBox()
        self.provider.addItems(["本机 Codex", "自定义 API（Chat Completions 兼容）"])
        self.provider.setCurrentIndex(1 if settings.get("provider") == "api" else 0)
        form.addRow("回复来源", self.provider)
        self.endpoint = QLineEdit(settings.get("endpoint", ""))
        self.endpoint.setPlaceholderText("https://你的服务/v1/chat/completions")
        form.addRow("完整 API 地址", self.endpoint)
        self.model = QLineEdit(settings.get("model", ""))
        form.addRow("模型名称", self.model)
        self.token = QLineEdit(parent.api_token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("留空则读取程序目录下的 api.txt")
        form.addRow("API Key", self.token)
        self.cwd = QLineEdit(settings.get("cwd", str(Path.home())))
        browse = QPushButton("选择…")
        browse.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.cwd)
        row.addWidget(browse)
        form.addRow("Codex 工作目录", row)
        self.auto_attach = QCheckBox("拖到窗口后吸附")
        self.auto_attach.setChecked(settings.get("attach", True))
        form.addRow(self.auto_attach)
        self.auto_open = QCheckBox("吸附后自动打开对话")
        self.auto_open.setChecked(settings.get("auto_open", True))
        form.addRow(self.auto_open)
        self.hints = QCheckBox("拖动时显示目标提示")
        self.hints.setChecked(settings.get("hints", True))
        form.addRow(self.hints)
        self.autostart = QCheckBox("登录桌面后自动启动")
        self.autostart.setChecked(autostart_file().exists())
        form.addRow(self.autostart)
        note = QLabel("吸附后会把该目录交给当前回复来源。自定义 API 可以读取、搜索、修改文件、运行命令和抓取网页；写入与命令会先确认。Codex 在该目录内可写，并可以访问网络，越权操作也会先确认。")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QHBoxLayout()
        save = QPushButton("保存")
        save.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        form.addRow(buttons)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", self.cwd.text())
        if path:
            self.cwd.setText(path)


def autostart_file():
    return CONFIG_DIR.parent / "autostart" / "orbit-codex.desktop"


class ChatWindow(QWidget):
    send = pyqtSignal(str)
    stop = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setObjectName("chatCard")
        self.setMinimumSize(320, 420)
        self.setMaximumSize(820, 900)
        self.resize(374, 494)
        self.setMouseTracking(True)
        self._resizing = None
        self._resize_until = 0.
        self._press_global = None
        self._press_geom = None
        self.accent = ACCENTS["other"]
        root = QVBoxLayout(self)
        root.setContentsMargins(17, 16, 17, 12)
        root.setSpacing(10)
        header = QHBoxLayout()
        self.icon = QLabel("✦")
        self.icon.setFixedSize(36, 36)
        self.icon.setAlignment(Qt.AlignCenter)
        header.addWidget(self.icon)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        self.title = QLabel("Orbit AI")
        self.title.setObjectName("chatTitle")
        self.context = QLabel("待命中 · 可以直接聊天")
        self.context.setObjectName("chatPath")
        self.context.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.context.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        heading.addWidget(self.title)
        heading.addWidget(self.context)
        header.addLayout(heading, 1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setFixedSize(29, 29)
        close.clicked.connect(self.close_requested.emit)
        header.addWidget(close)
        root.addLayout(header)
        banner = QWidget()
        banner.setObjectName("contextBanner")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(13, 10, 13, 10)
        banner_layout.setSpacing(2)
        self.context_title = QLabel("随时可以聊聊")
        self.context_title.setObjectName("contextTitle")
        self.context_text = QLabel("把我拖到窗口，或直接问我问题")
        self.context_text.setObjectName("contextText")
        self.context_text.setWordWrap(True)
        banner_layout.addWidget(self.context_title)
        banner_layout.addWidget(self.context_text)
        root.addWidget(banner)
        self.area = QScrollArea()
        self.area.setObjectName("messageArea")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area.verticalScrollBar().setSingleStep(28)
        self.messages = QWidget()
        self.message_layout = QVBoxLayout(self.messages)
        self.message_layout.setContentsMargins(2, 3, 2, 3)
        self.message_layout.setSpacing(9)
        self.message_layout.addStretch()
        self.area.setWidget(self.messages)
        root.addWidget(self.area, 1)
        self.prompts = QHBoxLayout()
        self.prompts.setSpacing(5)
        root.addLayout(self.prompts)
        self.status = QLabel("本机 Codex · 点击发送以连接")
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(40)
        self.status.setObjectName("status")
        root.addWidget(self.status)
        composer = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setPlaceholderText("让 Orbit 帮你做点什么...")
        self.input.returnPressed.connect(self.submit)
        composer.addWidget(self.input, 1)
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(36, 36)
        self.send_button.clicked.connect(self.submit)
        composer.addWidget(self.send_button)
        root.addLayout(composer)
        footer = QHBoxLayout()
        self.footnote = QLabel("✦ 本机 Codex · 拖到窗口后读取路径")
        self.footnote.setObjectName("footnote")
        footer.addWidget(self.footnote)
        footer.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setObjectName("sizeGrip")
        footer.addWidget(self.size_grip)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop.emit)
        self.stop_button.hide()
        footer.addWidget(self.stop_button)
        root.addLayout(footer)
        self.streaming = False
        self.current_bubble = None
        self.apply_theme(ACCENTS["other"])
        self.set_context(None)
        self.append("Codex", "你好！点击我可以随时聊天，也可以把我拖到窗口上一起工作。")

    def apply_theme(self, accent):
        self.accent = accent
        soft = QColor(accent)
        soft.setAlpha(28)
        banner = soft.name(QColor.HexArgb)
        self.setStyleSheet(f"""
            #chatCard {{ background: #fdfdff; border: 1px solid {accent}; border-radius: 19px; }}
            QLabel {{ color: #353448; background: transparent; font: 12px 'Noto Sans CJK SC'; }}
            #chatTitle {{ font-size: 14px; font-weight: 700; }}
            #chatPath, #footnote, #status {{ color: #8b879b; font-size: 10px; }}
            #contextBanner {{ background: {banner}; border-radius: 12px; }}
            #contextTitle {{ color: {accent}; font-weight: 700; }}
            #contextText {{ color: #8b82a7; font-size: 10px; }}
            QLineEdit {{ border: 1px solid #e1ddec; border-radius: 11px; padding: 8px 12px; background: #fff; color: #343448; }}
            QPushButton {{ border: 0; border-radius: 10px; padding: 7px 10px; background: #eeeaf8; color: #655696; font-size: 11px; }}
            QPushButton:hover {{ background: #e4def5; }}
            #sendButton {{ background: {accent}; color: #fff; font-size: 17px; font-weight: 700; }}
            #closeButton {{ background: transparent; color: #89849b; font-size: 20px; }}
            #sizeGrip {{ background: transparent; width: 14px; height: 14px; }}
            #messageArea, #messageArea QWidget {{ border: 0; background: #fdfdff; }}
            #messageArea QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px 4px 0; }}
            #messageArea QScrollBar::handle:vertical {{ background: rgba(138, 128, 168, 110); min-height: 36px; border-radius: 4px; }}
            #messageArea QScrollBar::handle:vertical:hover {{ background: {accent}; }}
            #messageArea QScrollBar::add-line:vertical, #messageArea QScrollBar::sub-line:vertical {{ height: 0; width: 0; background: transparent; border: 0; }}
            #messageArea QScrollBar::add-page:vertical, #messageArea QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.icon.setStyleSheet(f"background: {banner}; color: {accent}; border-radius: 10px; font-size: 18px;")

    def set_context(self, target):
        while self.prompts.count():
            item = self.prompts.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if target:
            title = target["title"]
            kind = target.get("kind", "other")
            self.apply_theme(target.get("accent") or ACCENTS.get(kind, ACCENTS["other"]))
            self.title.setText(f"已连接 · {title[:23]}")
            self.context.setText(target.get("path") or target.get("summary") or target["app"])
            self.context_title.setText(f"已读取{KIND_LABELS.get(kind, '窗口')}上下文")
            self.context_text.setText(target.get("summary") or f"{title} · {target['app']}")
            self.footnote.setText("✦ 可读取和修改该目录，写入与命令会先确认")
            prompts = CONTEXT_PROMPTS[kind]
        else:
            self.apply_theme(ACCENTS["other"])
            self.title.setText("Orbit AI")
            self.context.setText("待命中 · 可以直接聊天")
            self.context_title.setText("随时可以聊聊")
            self.context_text.setText("把我拖到窗口，或直接问我问题")
            self.footnote.setText("✦ 本机 Codex · 拖到窗口后读取路径")
            prompts = CONTEXT_PROMPTS[None]
        for prompt in prompts:
            button = QPushButton(prompt)
            button.clicked.connect(lambda _=False, value=prompt: self.send_prompt(value))
            self.prompts.addWidget(button)

    def send_prompt(self, value):
        if self.send_button.isEnabled():
            self.input.setText(value)
            self.submit()

    def clear_messages(self):
        self.reset_messages()
        self.append("Codex", "对话已清空。")

    def reset_messages(self):
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.current_bubble = None
        self.streaming = False

    def submit(self):
        value = self.input.text().strip()
        if value and self.send_button.isEnabled():
            self.input.clear()
            self.append("你", value)
            self.send_button.setEnabled(False)
            self.stop_button.show()
            self.send.emit(value)

    def append(self, author, text):
        bubble = QLabel(text)
        bubble.setTextFormat(Qt.PlainText)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setMaximumWidth(self.bubble_width())
        bubble.setContentsMargins(11, 9, 11, 9)
        if author == "你":
            bubble.setStyleSheet("background: #313040; color: white; border-radius: 12px; padding: 7px;")
        elif author == "错误":
            bubble.setStyleSheet("background: #fff0f1; color: #a94756; border-radius: 12px; padding: 7px;")
        elif author == "工具":
            bubble.setStyleSheet("background: #eef6f4; color: #2d6158; border-radius: 12px; padding: 7px;")
        else:
            bubble.setStyleSheet("background: #f0eff6; color: #414052; border-radius: 12px; padding: 7px;")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        if author == "你": layout.addStretch()
        layout.addWidget(bubble)
        if author != "你": layout.addStretch()
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))
        return bubble

    def add_delta(self, text):
        if not self.streaming:
            self.current_bubble = self.append("Codex", "")
            self.streaming = True
        self.current_bubble.setText(self.current_bubble.text() + text)
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))

    def finish(self, text):
        if not self.streaming: self.append("Codex", text)
        self.streaming = False
        self.current_bubble = None
        self.send_button.setEnabled(True)
        self.stop_button.hide()

    def bubble_width(self):
        return max(160, self.width() - 92)

    def is_user_resizing(self):
        return self._resizing is not None or time.monotonic() < self._resize_until

    def relayout_bubbles(self):
        if not hasattr(self, "message_layout"):
            return
        width = self.bubble_width()
        for index in range(self.message_layout.count()):
            row = self.message_layout.itemAt(index).widget()
            layout = row.layout() if row else None
            if layout is None:
                continue
            for child_index in range(layout.count()):
                widget = layout.itemAt(child_index).widget()
                if isinstance(widget, QLabel) and widget.wordWrap():
                    widget.setMaximumWidth(width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_until = time.monotonic() + 0.2
        self.relayout_bubbles()

    def _hit_edge(self, pos):
        margin = 7
        left = pos.x() <= margin
        right = pos.x() >= self.width() - margin
        top = pos.y() <= margin
        bottom = pos.y() >= self.height() - margin
        if not (left or right or top or bottom):
            return None
        return (left, right, top, bottom)

    def _cursor_for(self, edge):
        if edge is None:
            return Qt.ArrowCursor
        left, right, top, bottom = edge
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    def mousePressEvent(self, event):
        edge = self._hit_edge(event.pos()) if event.button() == Qt.LeftButton else None
        if edge:
            self._resizing = edge
            self._press_global = event.globalPos()
            self._press_geom = self.geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and event.buttons() & Qt.LeftButton:
            self._apply_resize(event.globalPos())
            event.accept()
            return
        self.setCursor(self._cursor_for(self._hit_edge(event.pos())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = None
            self._resize_until = time.monotonic() + 0.2
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_resize(self, global_pos):
        delta = global_pos - self._press_global
        geom = self._press_geom
        left, right, top, bottom = self._resizing
        x, y, width, height = geom.x(), geom.y(), geom.width(), geom.height()
        if left:
            width -= delta.x()
        if right:
            width += delta.x()
        if top:
            height -= delta.y()
        if bottom:
            height += delta.y()
        width = max(self.minimumWidth(), min(self.maximumWidth(), width))
        height = max(self.minimumHeight(), min(self.maximumHeight(), height))
        if left:
            x = geom.right() - width + 1
        if top:
            y = geom.bottom() - height + 1
        self.setGeometry(x, y, width, height)


class TetherWindow(QWidget):
    """Separate click-through surface so the black elastic band spans the desktop."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.start = None
        self.end = None
        self.broken = False
        self.release_started = None
        self.release_duration = .28
        self.flow = QTimer(self)
        self.flow.timeout.connect(self.tick)
        self.flow.start(16)

    def tick(self):
        if self.release_started and time.monotonic() - self.release_started >= self.release_duration:
            self.release_started = None
            self.hide()
        elif self.isVisible(): self.update()

    def release(self, duration=.28):
        if self.isVisible() and self.release_started is None:
            self.release_duration = duration
            self.release_started = time.monotonic()
            self.broken = True

    @staticmethod
    def ribbon(a, b, ra, rb, waist):
        dx, dy = b.x()-a.x(), b.y()-a.y()
        length = max(1, math.hypot(dx,dy))
        ux, uy = dx/length, dy/length
        nx, ny = -uy, ux
        pull = min(length*.22,40)
        points = lambda x,y,r: (x+nx*r,y+ny*r)
        mid = ((a.x()+b.x())/2,(a.y()+b.y())/2)
        p = QPainterPath()
        s,e,m = points(a.x(),a.y(),ra),points(b.x(),b.y(),rb),points(*mid,waist)
        p.moveTo(*s)
        p.cubicTo(s[0]+ux*pull,s[1]+uy*pull,m[0]-ux*pull,m[1]-uy*pull,*m)
        p.cubicTo(m[0]+ux*pull,m[1]+uy*pull,e[0]-ux*pull,e[1]-uy*pull,*e)
        s,e,m = points(a.x(),a.y(),-ra),points(b.x(),b.y(),-rb),points(*mid,-waist)
        p.lineTo(*e)
        p.cubicTo(e[0]-ux*pull,e[1]-uy*pull,m[0]+ux*pull,m[1]+uy*pull,*m)
        p.cubicTo(m[0]-ux*pull,m[1]-uy*pull,s[0]+ux*pull,s[1]+uy*pull,*s)
        p.closeSubpath()
        return p

    def update_points(self, start, end):
        distance = math.hypot(start.x() - end.x(), start.y() - end.y())
        if self.broken:
            return
        self.start = start
        self.end = end
        left = min(start.x(), end.x()) - 24
        top = min(start.y(), end.y()) - 24
        self.setGeometry(left, top, abs(start.x() - end.x()) + 48, abs(start.y() - end.y()) + 48)
        self.show()
        self.update()
        if distance > 220: self.release()

    def paintEvent(self, _event):
        if not self.start or not self.end:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        a = self.start - self.pos()
        b = self.end - self.pos()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = max(1, (dx * dx + dy * dy) ** 0.5)
        nx, ny = -dy / length, dx / length
        waist = max(2.5, 14 - length / 21)
        width_a, width_b = 14, 15
        mid_x, mid_y = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
        path = QPainterPath()
        path.moveTo(a.x() + nx * width_a, a.y() + ny * width_a)
        path.quadTo(mid_x + nx * waist, mid_y + ny * waist, b.x() + nx * width_b, b.y() + ny * width_b)
        path.lineTo(b.x() - nx * width_b, b.y() - ny * width_b)
        path.quadTo(mid_x - nx * waist, mid_y - ny * waist, a.x() - nx * width_a, a.y() - ny * width_a)
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2d2d31"))
        tension = max(0,min(1,(length-12)/208))
        waist = max(2.2,14-11.5*tension**.58 + math.sin(time.monotonic()*14)*1.15*math.sin(math.pi*tension))
        if self.release_started:
            progress = min(1,(time.monotonic()-self.release_started)/self.release_duration)
            eased = 1-(1-progress)**3
            reach = .5-.47*eased
            painter.setOpacity(min(1,(1-progress)*1.5))
            from PyQt5.QtCore import QPointF
            tip_a = QPointF(a.x()+dx*reach,a.y()+dy*reach)
            tip_b = QPointF(b.x()-dx*reach,b.y()-dy*reach)
            painter.drawPath(self.ribbon(a,tip_a,13*(1-eased*.5),max(.3,8*(1-eased)),max(.3,waist*(1-eased))))
            painter.drawPath(self.ribbon(b,tip_b,16*(1-eased*.5),max(.3,8*(1-eased)),max(.3,waist*(1-eased))))
            for i in range(3):
                spread=(i-1)*(9+22*eased)
                painter.drawEllipse(QPointF(mid_x+nx*spread,mid_y+ny*spread),float(4-i),float(4-i))
        else:
            painter.drawPath(self.ribbon(a,b,16-tension*5,18-tension*4,waist))
        painter.end()


class TargetOverlay(QWidget):
    """A click-through target outline and hint while the mascot is dragged."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.target = None
        self.show_hint = True
        self.connected = False
        self.accent = "#8a72d5"
        self.connected_at = None
        self.pulse = QTimer(self)
        self.pulse.timeout.connect(self.update)
        self.pulse.start(16)

    def set_target(self, target, show_hint=True, connected=False):
        if not target:
            self.target = None
            self.connected = False
            self.connected_at = None
            self.hide()
            return
        switched = not self.target or self.target.get("id") != target.get("id") or self.connected != connected
        self.target = target
        self.show_hint = show_hint
        if connected and not self.connected:
            self.connected_at = time.monotonic()
        elif not connected:
            self.connected_at = None
        self.connected = connected
        self.accent = target.get("accent") or ACCENTS.get(target.get("kind"), "#8a72d5")
        rect = target["rect"]
        pad = 26
        self.setGeometry(rect.adjusted(-pad, -pad, pad, pad))
        if switched:
            self.show()
        self.update()

    def paintEvent(self, _event):
        if not self.target:
            return
        pad = 26
        inner = QRect(pad, pad, self.width() - pad * 2, self.height() - pad * 2)
        phase = (1 - math.cos(time.monotonic() * 2 * math.pi / 1.2)) / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(self.accent)
        if not self.connected:
            glow = QColor(accent)
            glow.setAlpha(int(16 + 30 * phase))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            spread = int(8 + 12 * phase)
            p.drawRoundedRect(inner.adjusted(-spread, -spread, spread, spread), 16, 16)
            p.setPen(QPen(accent, int(3 + phase)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(inner.adjusted(2, 2, -3, -3), 8, 8)
        else:
            p.setPen(QPen(accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(inner.adjusted(1, 1, -2, -2), 8, 8)
            if self.connected_at:
                sweep = (time.monotonic() - self.connected_at) / .85
                if 0 <= sweep <= 1:
                    band = QColor(accent)
                    band.setAlpha(int(70 * (1 - sweep)))
                    p.setPen(Qt.NoPen)
                    p.setBrush(band)
                    p.drawRoundedRect(int(inner.left() + (inner.width() + 40) * sweep - 50), inner.top(), 42, 28, 8, 8)
        if self.show_hint or self.connected:
            text = ("✦  ORBIT 已连接" if self.connected else f"✦  松开以连接 · {self.target['title'][:30]}")
            metrics = p.fontMetrics()
            width = min(inner.width() - 20, metrics.horizontalAdvance(text) + 28)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#fbfaff"))
            p.drawRoundedRect(inner.left() + 12, inner.top() + 12, width, 32, 10, 10)
            p.setPen(accent)
            p.drawText(QRect(inner.left() + 24, inner.top() + 12, width - 16, 32), Qt.AlignVCenter, text)
        p.end()


class HomeNotch(QWidget):
    """Smooth inverted trapezoid attached to the top of the desktop."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(116, 74)
        self.started = time.monotonic()
        self.closing = None
        self.hover = 0.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

    def reveal(self, screen):
        self.closing = None
        self.move(screen.center().x()-self.width()//2, screen.top())
        self.started = time.monotonic()
        self.show()

    def dismiss(self):
        """Jelly-close the envelope after the mascot is pulled out of it."""
        if self.closing is not None or not self.isVisible():
            return
        self.closing = time.monotonic()
        self.raise_()

    def _close_pose(self, progress):
        keys = (
            (0.00, 1.00, 1.00),
            (0.14, 1.36, 1.10),
            (0.36, 0.06, 0.62),
            (0.52, 0.62, 1.08),
            (0.70, 0.00, 0.70),
            (0.84, 0.28, 0.94),
            (1.00, 0.00, 0.55),
        )
        for start, end in zip(keys, keys[1:]):
            if progress <= end[0]:
                span = end[0] - start[0]
                blend = 0 if span == 0 else (progress - start[0]) / span
                return start[1] + (end[1] - start[1]) * blend, start[2] + (end[2] - start[2]) * blend
        return 0., 0.55

    def paintEvent(self, event):
        now = time.monotonic()
        if self.closing is not None:
            elapsed = now - self.closing
            if elapsed >= 0.62:
                self.closing = None
                QTimer.singleShot(0, self.hide)
                return
            depth_scale, width_scale = self._close_pose(elapsed / 0.62)
            depth = 50 * depth_scale
        else:
            t = min(1., (now-self.started)/.62)
            spring = 1 + 2.4 * (t - 1) ** 3 + 1.4 * (t - 1) ** 2
            inside = self.geometry().contains(QCursor.pos())
            self.hover += ((1. if inside else 0.)-self.hover)*.18
            depth = 50 * spring + 5*self.hover
            width_scale = 1.
        if depth < 0.8:
            return
        center = 58
        half = 20 * width_scale
        left, right = center - half, center + half
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0e0e11"))
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(116, 0)
        path.cubicTo(right + 16, 0, right + 23, depth, right, depth)
        path.lineTo(left, depth)
        path.cubicTo(left - 23, depth, left - 16, 0, 0, 0)
        path.closeSubpath()
        p.drawPath(path)
        p.end()


def api_error_text(error):
    if isinstance(error, urllib.error.HTTPError):
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
            message = payload.get("error", {}).get("message")
            if isinstance(message, str):
                detail = message.strip()
        except (OSError, ValueError, AttributeError, UnicodeError):
            detail = ""
        return f"HTTP {error.code}" + (f"：{detail}" if detail else "")
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error)


class Pet(QWidget):
    api_result = pyqtSignal(int, object)
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        # Keep a generous transparent canvas around the smaller painted mascot.
        self.setFixedSize(112, 118)
        self.pet_scale = .78
        self.pet_padding = 24
        self.docked = True
        self.drag_growth = 1.
        self.drag_from_home = False
        self.next_idle = time.monotonic() + random.uniform(4, 9)
        self.idle_until = 0.
        self.idle_kind = "blink"
        self.gaze_x = self.gaze_y = 0.
        self.notch = HomeNotch()
        self.settings = self.load_settings()
        self.api_token = load_api_token()
        self.api_generation = 0
        self.api_round = 0
        self.api_messages = []
        self.api_inflight = False
        self.api_tool_running = False
        self.api_history = []
        self.api_result.connect(self.api_finished)
        self.context_pending = False
        self.pending_message = None
        self.bridge = CodexBridge()
        self.chat = ChatWindow()
        self.chat.send.connect(self.ask)
        self.chat.stop.connect(self.stop_reply)
        self.chat.close_requested.connect(self.close_chat)
        self.bridge.status.connect(self.on_status)
        self.bridge.delta.connect(self.chat.add_delta)
        self.bridge.finished.connect(self.on_finished)
        self.bridge.error.connect(self.on_error)
        self.tether = TetherWindow()
        self.overlay = TargetOverlay()
        self.drag_start = None
        self.dragged = False
        self.target = None
        self.hover_target = None
        self.cached_windows = []
        self.held_id = None
        self.context_token = 0
        self.misses = 0
        self.dock_started = None
        self._mask_key = None
        self.tether_from = None
        self.mood = "idle"
        self.mood_started = time.monotonic()
        self.landing_started = None
        self.phase = 0
        self.bounce = 0
        self.live_rect = None
        self.grip = 0.
        self.slip = 0.
        self.slip_v = 0.
        self.lean = 0.
        self.lean_v = 0.
        self.ride_vx = 0.
        self.ride_vy = 0.
        self.ride_at = 0.
        self._frame_at = time.monotonic()
        self.animation = QPropertyAnimation(self, b"pos", self)
        self.animation.setEasingCurve(QEasingCurve.OutBack)
        self.animation.setDuration(500)
        self.animation.finished.connect(self.finish_return)
        curve = QEasingCurve(QEasingCurve.OutBack)
        curve.setOvershoot(1.35)
        self.chat_motion = QPropertyAnimation(self.chat, b"pos", self)
        self.chat_motion.setEasingCurve(curve)
        self.chat_fade = QPropertyAnimation(self.chat, b"windowOpacity", self)
        self.chat_fade.setEasingCurve(QEasingCurve.OutCubic)
        self.probe = WindowProbe()
        self.probe.found.connect(self.on_windows)
        self.probe.placed.connect(self.on_window_moved)
        self.context_bridge = ContextBridge()
        self.context_bridge.ready.connect(self.apply_context)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.follow_target)
        self.timer.start(200)
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.request_windows)
        self.scan_timer.start(700)
        self.motion = QTimer(self)
        self.motion.timeout.connect(self.animate_pet)
        self.motion.start(16)
        screen = QApplication.primaryScreen().availableGeometry()
        self.home = QPoint(screen.center().x() - 56, screen.top())
        self.move(self.home)
        self.notch.reveal(screen)
        self.show()
        self.refresh_mask()
        QTimer.singleShot(200, self.request_windows)

    def load_settings(self):
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            return {"cwd": str(Path.home()), "attach": True, "auto_open": True, "hints": True}

    def animate_pet(self):
        now = time.monotonic()
        if self.mood == "idle" and not self.drag_start and now >= self.next_idle:
            self.idle_kind = random.choice(["blink", "hop"])
            self.idle_until = now + .9
            self.next_idle = now + random.uniform(5, 12)
        center = self.visual_center()
        delta = QCursor.pos()-center
        distance = math.hypot(delta.x(), delta.y())
        if distance > 1:
            gx = max(-8.5, min(8.5, delta.x() / 18))
            gy = max(-5.5, min(5.5, delta.y() / 22))
        else:
            gx = gy = 0.
        self.gaze_x += (gx-self.gaze_x)*.28
        self.gaze_y += (gy-self.gaze_y)*.28
        self.phase += 0.16
        if self.bounce > 0:
            self.bounce = max(0, self.bounce - 1)
        frame = min(0.05, max(0.001, now - self._frame_at))
        self._frame_at = now
        if now - self.ride_at > 0.09:
            self.ride_vx *= 0.84
            self.ride_vy *= 0.84
        speed = math.hypot(self.ride_vx, self.ride_vy)
        grip_target = min(1., max(0., (speed - 36) / 620))
        self.grip += (grip_target - self.grip) * min(1., frame * 10)
        if grip_target > 0.1:
            lean_target = max(-7., min(7., -self.ride_vx / 140))
            slip_target = max(-10., min(6., -self.ride_vy / 170))
            self.lean += (lean_target - self.lean) * min(1., frame * 14)
            self.slip += (slip_target - self.slip) * min(1., frame * 14)
            self.lean_v = self.slip_v = 0.
        else:
            self.slip_v += (-36 * self.slip - 9 * self.slip_v) * frame
            self.slip += self.slip_v * frame
            self.lean_v += (-38 * self.lean - 9 * self.lean_v) * frame
            self.lean += self.lean_v * frame
        self.refresh_mask()
        self.update()

    def placement(self):
        """Return the mascot's unscaled origin and scale inside the widget."""
        scale_free = self.pet_scale * (self.drag_growth if self.drag_from_home and self.dragged else 1)
        cy = 24 + 68 * (self.drag_growth - .5) if self.drag_from_home and self.dragged else 58
        free = (56 - 41 * scale_free, cy - 43 * scale_free, scale_free)
        scale_dock = self.pet_scale * .5
        dock = (self.width() / 2 - 41 * scale_dock, 24 - 36 * scale_dock, scale_dock)
        if self.dock_started:
            progress = min(1, (time.monotonic() - self.dock_started) / .32)
            ease = 1 - (1 - progress) ** 3
            if progress >= 1:
                self.dock_started = None
                return dock
            return tuple(free[i] + (dock[i] - free[i]) * ease for i in range(3))
        return dock if self.docked else free

    def visual_center(self):
        origin_x, origin_y, scale = self.placement()
        return self.pos() + QPoint(round(origin_x + 41 * scale), round(origin_y + 43 * scale))

    def contact_y(self):
        """Widget y of the window edge. The head stays above it; the arms hang below it."""
        scale = self.pet_scale
        return 58 - 43 * scale + 72 * scale

    def refresh_mask(self):
        origin_x, origin_y, scale = self.placement()
        body = QRect(int(origin_x - 8), int(origin_y - 6), int(90 * scale + 16), int(96 * scale + 18))
        region = QRegion(body, QRegion.Ellipse)
        perched = bool(self.target) and not self.docked and not self.drag_start
        if perched:
            for paw_x in (16, 48):
                paw = QRect(int(origin_x + paw_x * scale) - 2, int(origin_y + 60 * scale), int(22 * scale) + 4, int(28 * scale))
                region = region.united(QRegion(paw, QRegion.Ellipse))
        if self.mood in ("thinking", "working", "done"):
            badge = QRect(int(origin_x + 56 * scale), int(origin_y - 6 * scale), int(28 * scale), int(28 * scale))
            region = region.united(QRegion(badge, QRegion.Ellipse))
        key = (body.getRect(), self.mood in ("thinking", "working", "done"), perched)
        if key == self._mask_key:
            return
        self._mask_key = key
        self.setMask(region)

    def request_windows(self):
        own_ids = {int(widget.winId()) for widget in QApplication.topLevelWidgets()}
        self.probe.request(own_ids)

    def on_windows(self, seq, windows):
        if seq != self.probe.seq:
            return
        self.cached_windows = [hydrate_window(window) for window in windows]

    def begin_context(self, window):
        self.context_token += 1
        self.context_pending = True
        token = self.context_token
        snapshot = {key: window.get(key) for key in ("id", "title", "app", "pid", "cwd", "kind")}
        rect = window["rect"]
        snapshot["rect"] = (rect.x(), rect.y(), rect.width(), rect.height())
        threading.Thread(target=self._capture_context, args=(token, window["id"], snapshot), daemon=True).start()

    def _capture_context(self, token, window_id, snapshot):
        try:
            context = capture_context(snapshot)
        except Exception:
            context = {"kind": snapshot.get("kind") or "other", "accent": ACCENTS["other"], "summary": "没能读取这个窗口的路径"}
        self.context_bridge.ready.emit(token, window_id, context)

    def apply_context(self, token, window_id, context):
        if token != self.context_token or not self.target or self.target.get("id") != window_id:
            return
        self.target.update(context)
        self.context_pending = False
        self.chat.set_context(self.target)
        if self.chat.message_layout.count() <= 2:
            self.chat.reset_messages()
            self.chat.append("Codex", self.connection_message(self.target))
        self.overlay.set_target(self.target, False, connected=True)
        if self.pending_message:
            message = self.pending_message
            self.pending_message = None
            self.ask(message)

    def connection_message(self, target):
        path = target.get("path")
        kind = target.get("kind")
        if path and kind == "files":
            names = "、".join(item.rstrip("/") for item in (target.get("entries") or [])[:8])
            listing = f"我看到这些项目：{names}。" if names else ""
            return f"已连接「{target['title']}」。路径是 {path}。{listing}可以直接让我处理这里的文件。"
        if path and kind == "editor":
            read = "我已经读到文件开头。" if target.get("preview") else "这个文件不是可预览的文本。"
            return f"已连接「{target['title']}」。当前文件是 {path}。{read}"
        if path and kind == "terminal":
            return f"已连接终端「{target['title']}」。当前目录是 {path}。"
        if target.get("url"):
            return f"已连接「{target['title']}」。页面地址是 {target['url']}。"
        if path:
            return f"已连接「{target['title']}」。相关路径是 {path}。"
        return f"已连接「{target['title']}」。我认出了{KIND_LABELS.get(kind, '窗口')}，但没有解析出可操作的路径。"

    def set_mood(self, mood):
        if self.mood != mood: self.mood_started = time.monotonic()
        self.mood = mood
        self.update()

    def animate_to(self, point, duration=500):
        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(point)
        self.animation.start()

    def go_home(self):
        self.animate_to(self.home, 560)

    def finish_return(self):
        if not self.target and not self.drag_start and not self.dragged and self.pos() == self.home:
            self.docked = True
            self.dock_started = time.monotonic()
            self.notch.reveal(QApplication.primaryScreen().availableGeometry())
            self.refresh_mask()
            self.update()
            self.raise_()

    def move_chat(self):
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        if self.target:
            rect = self.target["rect"]
            right = rect.right() + 17
            left = rect.left() - self.chat.width() - 17
            y_anchor = rect.top() + 18
        else:
            right = self.x() + self.width() + 12
            left = self.x() - self.chat.width() - 12
            y_anchor = self.y() + self.height() + 12
        x = right if right + self.chat.width() <= area.right() - 8 else left
        x = max(area.left() + 8, min(x, area.right() - self.chat.width() - 8))
        y = max(area.top() + 8, min(y_anchor, area.bottom() - self.chat.height() - 8))
        if not self.chat.is_user_resizing():
            self.chat.move(x, y)

    def place_on_window(self, rect, allow_bounce=False):
        """Move the mascot and the chat together so they stay locked to the window."""
        destination = QPoint(rect.center().x() - self.width() // 2, rect.top() - round(self.contact_y()))
        if self.pos() != destination:
            if allow_bounce and self.bounce:
                self.animate_to(destination, 420)
            else:
                self.animation.stop()
                self.move(destination)
        if not self.chat.isVisible():
            return
        opening = self.chat_motion.state() == QAbstractAnimation.Running
        if opening and allow_bounce:
            return
        if opening:
            self.chat_motion.stop()
            self.chat.setWindowOpacity(1)
        self.move_chat()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        origin_x, origin_y, scale = self.placement()
        painter.translate(origin_x, origin_y)
        painter.scale(scale, scale)
        elapsed = time.monotonic() - self.mood_started
        painter.save()
        y, angle, sx, sy = 0., 0., 1., 1.
        wave = lambda duration: (1 - math.cos(elapsed * 2 * math.pi / duration)) / 2
        holding = self.target and not self.docked and (self.grip > 0.08 or abs(self.slip) > 0.45 or abs(self.lean) > 0.45)
        if self.mood == "idle" and not self.docked:
            if holding:
                y = self.slip * 0.85
                angle = self.lean
                sx, sy = 1 + .07 * self.grip, 1 - .1 * self.grip
            elif self.target:
                y, sx, sy = 2 * wave(3.8), 1 + .025 * wave(3.8), 1 - .02 * wave(3.8)
            else:
                y, sx, sy = -6 * wave(3.6), 1 + .035 * wave(3.6), 1 - .02 * wave(3.6)
        elif self.mood == "dragging":
            sx, sy = 1.06 + .04 * wave(.68), .94 + .04 * (1 - wave(.68))
        elif self.mood == "thinking":
            y, sx, sy = -3 * wave(1.1), 1 + .03 * wave(1.1), 1 - .04 * wave(1.1)
        elif self.mood == "working":
            y, sx, sy = 2 * wave(.48), 1 + .04 * wave(.48), 1 - .05 * wave(.48)
        idle_progress = max(0., min(1., 1-(self.idle_until-time.monotonic())/.9))
        idle_wave = math.sin(idle_progress*math.pi) if time.monotonic()<self.idle_until else 0.
        if self.mood == "idle" and self.idle_kind == "hop":
            y -= 6 * idle_wave
            sx += .04 * idle_wave
            sy -= .06 * idle_wave
        frames = None
        progress = 1.
        if self.landing_started is not None:
            progress = (time.monotonic() - self.landing_started) / .78
            frames = [(0,-28,0,.65,1.45),(.27,8,0,1.34,.66),(.48,-12,0,.82,1.25),(.66,4,0,1.13,.85),(.82,-3,0,.94,1.07),(1,0,0,1,1)]
        elif self.mood == "done":
            progress = elapsed / .68
            frames = [(0,0,0,1,1),(.35,-9,0,.94,1.1),(.7,2,0,1.08,.9),(1,0,0,1,1)]
        if frames and progress < 1:
            for a, b in zip(frames, frames[1:]):
                if a[0] <= progress <= b[0]:
                    t = (progress-a[0])/(b[0]-a[0])
                    y, angle, sx, sy = [a[i]+(b[i]-a[i])*t for i in range(1,5)]
                    break
        elif self.landing_started is not None:
            self.landing_started = None
        if holding and self.mood != "idle" and not (frames and progress < 1):
            y += self.slip * 0.6
            angle += self.lean * 0.65
            sx += .05 * self.grip
            sy -= .07 * self.grip
        painter.translate(41, 43+y)
        painter.rotate(angle)
        painter.scale(sx, sy)
        painter.translate(-41,-43)
        if self.landing_started is not None and progress < 1:
            ring = QColor(self.target.get("accent") if self.target else "#8370da")
            ring.setAlpha(int(180 * (1 - progress)))
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.NoBrush)
            grow = 6 + 22 * progress
            painter.drawEllipse(int(11 - grow), int(11 - grow), int(60 + grow * 2), int(60 + grow * 2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2d2d31"))
        lift_left = lift_right = 0
        if self.target and not self.docked:
            tap = (1 - math.cos(elapsed * 2 * math.pi / .48)) / 2 if self.mood == "working" else 0
            lift_left = 5 * tap if self.mood == "working" else 0
            lift_right = 5 * (1 - tap) if self.mood == "working" else 0
            dig = 5 * self.grip + max(0., -self.slip) * 0.45
            spread = 2.6 * self.grip
            painter.drawEllipse(int(16 - spread), int(66 + dig - lift_left), 18, 16)
            painter.drawEllipse(int(48 + spread), int(66 + dig - lift_right), 18, 16)
        painter.drawEllipse(11, 11, 60, 60)
        if self.target and not self.docked:
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
            painter.drawArc(int(20 - spread), int(74 + dig - lift_left), 10, 5, 200 * 16, 140 * 16)
            painter.drawArc(int(52 + spread), int(74 + dig - lift_right), 10, 5, 200 * 16, 140 * 16)
            painter.setPen(Qt.NoPen)
        ear_cycle = time.monotonic() % 6
        ear = 0.
        if self.target and self.mood == "idle" and 5.2 <= ear_cycle <= 5.7:
            ear = math.sin((ear_cycle - 5.2) / .5 * math.pi) * 9
        painter.save()
        painter.translate(21, 15)
        painter.rotate((-38 if self.mood == "dragging" else -26) - ear)
        painter.drawRoundedRect(-8, -13, 16, 25, 7, 7)
        painter.restore()
        painter.save()
        painter.translate(61, 15)
        painter.rotate((38 if self.mood == "dragging" else 26) + ear)
        painter.drawRoundedRect(-8, -13, 16, 25, 7, 7)
        painter.restore()
        painter.setBrush(QColor("white"))
        eye_y = 37 if self.mood != "thinking" else 34
        if self.mood == "done":
            painter.setPen(QPen(QColor("white"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(27, 39, 9, 6, 0, 180 * 16)
            painter.drawArc(47, 39, 9, 6, 0, 180 * 16)
            painter.drawArc(38, 49, 8, 5, 180 * 16, 180 * 16)
            painter.setPen(Qt.NoPen)
        else:
            eye_h = 1 if self.mood == "idle" and self.idle_kind == "blink" and idle_wave > .85 else 8
            painter.drawEllipse(round(27+self.gaze_x), round(eye_y+self.gaze_y), 6, eye_h)
            painter.drawEllipse(round(49+self.gaze_x), round(eye_y+self.gaze_y), 6, eye_h)
        if self.mood in ("thinking", "working", "done"):
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(58, -2, 24, 24)
            painter.setPen(QColor("#6c62a0" if self.mood != "done" else "#4aa882"))
            painter.setFont(QFont("Sans", 11, QFont.Bold))
            painter.drawText(QRect(58, -2, 24, 24), Qt.AlignCenter, {"thinking": "…", "working": "✦", "done": "✓"}[self.mood])
        painter.restore()
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.animation.stop()
            self.drag_start = event.globalPos() - self.pos()
            self.press_point = event.globalPos()
            self.press_center = self.visual_center()
            self.drag_from_home = self.docked
            self.held_id = self.target["id"] if self.target else None
            self.dock_started = None
            self.tether_from = self.press_center
            self.dragged = False
            self.tether.broken = False
            self.tether.release_started = None
            self.request_windows()

    def mouseMoveEvent(self, event):
        if self.drag_start and event.buttons() & Qt.LeftButton:
            new_pos = event.globalPos() - self.drag_start
            if (event.globalPos() - (self.pos() + self.drag_start)).manhattanLength() > 4 or self.dragged:
                if self.drag_from_home:
                    self.notch.dismiss()
                else:
                    self.notch.hide()
                self.dragged = True
                self.docked = False
                self.dock_started = None
                if self.drag_from_home:
                    delta = event.globalPos() - self.press_point
                    distance = math.hypot(delta.x(), delta.y())
                    progress = max(0., min(1., distance / 220))
                    self.drag_growth = .5 + .5 * progress * progress * (3 - 2 * progress)
                    offset = self.visual_center() - self.pos()
                    new_pos = self.press_center + delta - offset
                self.target = None
                self.move(new_pos)
                self.tether.update_points(self.tether_from, self.visual_center())
                self.set_mood("dragging")
                pool = self.cached_windows if self.settings.get("attach", True) else []
                self.hover_target = topmost_at(pool, self.visual_center())
                self.overlay.set_target(self.hover_target, self.settings.get("hints", True))
                self.chat.hide()
                self.raise_()
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        was_dragged = self.dragged
        self.dragged = False
        self.drag_start = None
        self.tether_from = None
        self.tether.release(.19)
        self.overlay.set_target(None)
        self.update()
        if was_dragged:
            self.target = self.hover_target
            self.hover_target = None
            if self.target and self.target["id"] == self.held_id:
                self.bounce = 10
                self.landing_started = time.monotonic()
                self.probe.watch(self.target["id"])
                self.follow_target()
                if self.settings.get("auto_open", True):
                    QTimer.singleShot(260, self.open_chat)
            elif self.target:
                self.cancel_api()
                self.api_history.clear()
                self.bridge.invalidate()
                self.chat.set_context(self.target)
                self.chat.reset_messages()
                self.chat.append("Codex", f"已放在「{self.target['title']}」上，正在读取路径和内容…")
                self.chat.send_button.setEnabled(True)
                self.chat.stop_button.hide()
                self.begin_context(self.target)
                self.bounce = 10
                self.landing_started = time.monotonic()
                self.probe.watch(self.target["id"])
                self.follow_target()
                if self.settings.get("auto_open", True):
                    QTimer.singleShot(260, self.open_chat)
            else:
                self.probe.unwatch()
                self.live_rect = None
                self.context_token += 1
                self.context_pending = False
                self.pending_message = None
                self.cancel_api()
                self.api_history.clear()
                self.bridge.invalidate()
                self.chat.set_context(None)
                self.chat.reset_messages()
                self.chat.append("Codex", "已回到待命状态。你可以直接聊天，或把我拖到另一个窗口。")
                self.chat.send_button.setEnabled(True)
                self.chat.stop_button.hide()
                self.go_home()
            self.set_mood("idle")
            return
        if self.chat.isVisible(): self.chat.hide()
        else: self.open_chat()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("打开对话", self.open_chat)
        menu.addAction("设置", self.open_settings)
        menu.addAction("回到顶部", self.detach)
        menu.addAction("清空对话", self.clear_conversation)
        if self.target:
            menu.addAction("断开窗口", self.detach)
        menu.addSeparator()
        menu.addAction("退出 Orbit", QApplication.instance().quit)
        menu.exec_(event.globalPos())

    def open_chat(self):
        self.move_chat()
        destination = self.chat.pos()
        opening = not self.chat.isVisible()
        if opening:
            self.chat.move(destination.x(), destination.y() - 18)
            self.chat.setWindowOpacity(0)
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.input.setFocus(Qt.ActiveWindowFocusReason)
        QTimer.singleShot(0, self.chat.input.setFocus)
        if opening:
            self.chat_motion.stop()
            self.chat_motion.setDuration(420)
            self.chat_motion.setStartValue(self.chat.pos())
            self.chat_motion.setEndValue(destination)
            self.chat_motion.start()
            self.chat_fade.stop()
            self.chat_fade.setDuration(280)
            self.chat_fade.setStartValue(0)
            self.chat_fade.setEndValue(1)
            self.chat_fade.start()

    def close_chat(self):
        self.chat.hide()
        if self.target: self.detach()

    def clear_conversation(self):
        self.cancel_api()
        self.pending_message = None
        self.api_history.clear()
        self.bridge.invalidate()
        self.chat.clear_messages()
        self.chat.send_button.setEnabled(True)
        self.chat.stop_button.hide()
        self.set_mood("idle")

    def detach(self):
        self.context_token += 1
        self.context_pending = False
        self.pending_message = None
        self.cancel_api()
        self.api_history.clear()
        self.bridge.invalidate()
        self.target = None
        self.live_rect = None
        self.probe.unwatch()
        self.grip = self.slip = self.lean = 0.
        self.ride_vx = self.ride_vy = 0.
        self.overlay.set_target(None)
        self.chat.set_context(None)
        self.chat.send_button.setEnabled(True)
        self.chat.stop_button.hide()
        self.chat.hide()
        self.go_home()
        self.set_mood("idle")
        self.update()

    def follow_target(self):
        if not self.target or self.drag_start:
            return
        if not self.cached_windows:
            return
        current = next((window for window in self.cached_windows if window["id"] == self.target["id"]), None)
        if not current:
            self.misses += 1
            if self.misses >= 3:
                self.detach()
            return
        self.misses = 0
        title_changed = current["title"] != self.target.get("title")
        preserved = {key: self.target.get(key) for key in ("path", "entries", "preview", "summary", "accent", "url")}
        self.target = current
        if not title_changed:
            self.target.update({key: value for key, value in preserved.items() if value is not None})
        rect = self.live_rect if self.live_rect is not None else current["rect"]
        self.target["rect"] = rect
        self.overlay.set_target(self.target, False, connected=True)
        self.place_on_window(rect, allow_bounce=True)
        if title_changed:
            self.begin_context(self.target)
        self.update()

    def on_window_moved(self, wid, rect):
        if self.drag_start or not self.target or self.target.get("id") != wid:
            return
        if rect is None:
            self.misses += 1
            if self.misses >= 4:
                self.detach()
            return
        self.misses = 0
        new_rect = QRect(*rect)
        old = self.target.get("rect")
        moved = old is None or old.topLeft() != new_rect.topLeft() or old.size() != new_rect.size()
        if moved and old is not None:
            now = time.monotonic()
            dt = max(0.02, now - self.ride_at) if self.ride_at else 0.04
            self.ride_at = now
            self.ride_vx = (new_rect.center().x() - old.center().x()) / dt
            self.ride_vy = (new_rect.top() - old.top()) / dt
        self.live_rect = new_rect
        self.target["rect"] = new_rect
        for window in self.cached_windows:
            if window["id"] == wid:
                window["rect"] = QRect(new_rect)
                break
        self.overlay.set_target(self.target, False, connected=True)
        self.place_on_window(new_rect, allow_bounce=not moved)

    def working_directory(self):
        fallback = Path(self.settings.get("cwd") or Path.home()).expanduser()
        if self.target and self.target.get("path"):
            candidate = Path(self.target["path"])
            folder = candidate if candidate.is_dir() else candidate.parent
            try:
                if folder.is_dir() and os.access(folder, os.W_OK):
                    return str(folder.resolve())
            except OSError:
                pass
        if fallback.is_dir():
            return str(fallback)
        return str(Path.home())

    def ask(self, message):
        if self.target and self.context_pending:
            self.pending_message = message
            self.chat.status.setText("正在读取窗口路径…")
            return
        self.set_mood("thinking")
        QTimer.singleShot(500, lambda: self.set_mood("working") if not self.chat.send_button.isEnabled() else None)
        if self.settings.get("provider") == "api":
            self.ask_api(message)
            return
        cwd = self.working_directory()
        if not Path(cwd).is_dir():
            self.on_error("工作目录不存在，请在设置中重新选择。")
            return
        self.bridge.ask(message, cwd, self.target)

    def cancel_api(self):
        self.api_generation += 1
        self.api_inflight = False
        self.api_tool_running = False

    def ask_api(self, message):
        raw = self.settings.get("endpoint", "").strip()
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or not self.settings.get("model"):
            self.on_error("请在设置中填写完整 API 地址和模型名称。")
            return
        self.api_generation += 1
        self.api_round = 0
        self.api_user_message = message
        context = (
            "你是 Orbit 桌面助手，用中文回复。需要查看或修改文件时调用工具，不要假装已经读过或改过。"
            "写入文件和运行命令会先请用户确认。敏感文件会读取失败。浏览器没有正文时，用 fetch_url 读取 http 或 https 地址。\n"
            + context_prompt(self.target, self.working_directory())
        )
        messages = [{"role": "system", "content": context}] + self.api_history[-20:] + [{"role": "user", "content": message}]
        self.post_api(messages)

    def post_api(self, messages):
        self.api_messages = messages
        self.api_inflight = True
        body = {"model": self.settings["model"], "messages": messages, "tools": API_TOOLS, "stream": False}
        self.chat.status.setText("API 正在请求…")
        threading.Thread(
            target=self._post_api,
            args=(self.api_generation, self.api_user_message, self.settings.get("endpoint", "").strip(), self.api_token, body),
            daemon=True,
        ).start()

    def _post_api(self, generation, message, url, token, body):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read()
            self.api_result.emit(generation, {"ok": True, "message": message, "body": payload})
        except Exception as error:
            self.api_result.emit(generation, {"ok": False, "error": api_error_text(error)})

    def api_finished(self, generation, result):
        if generation != self.api_generation:
            return
        self.api_inflight = False
        if not result.get("ok"):
            self.on_error("API 请求失败：" + result.get("error", "未知错误"))
            return
        try:
            data = json.loads(result["body"].decode("utf-8"))
            message_obj = data["choices"][0]["message"] or {}
            tool_calls = message_obj.get("tool_calls") or []
            if tool_calls and self.api_round < 8:
                self.continue_tools(message_obj, tool_calls)
                return
            answer = message_obj.get("content") or ""
            if not isinstance(answer, str) or not answer.strip():
                answer = message_obj.get("reasoning_content") or ""
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("接口返回空回复")
            self.api_history.extend([{"role": "user", "content": result["message"]}, {"role": "assistant", "content": answer}])
            self.on_finished(answer)
            self.chat.status.setText("API 已就绪")
        except (ValueError, KeyError, IndexError, TypeError, UnicodeError) as error:
            self.on_error("API 请求失败：" + str(error))

    def continue_tools(self, message_obj, tool_calls):
        self.api_round += 1
        generation = self.api_generation
        self.api_tool_running = True
        root = self.working_directory()
        blocked = bool(self.target and not self.target.get("path"))
        content = message_obj.get("content") or ""
        assistant = {"role": "assistant", "content": message_obj.get("content"), "tool_calls": tool_calls}
        if message_obj.get("reasoning_content"):
            assistant["reasoning_content"] = message_obj["reasoning_content"]
        if content.strip():
            self.chat.append("Codex", content.strip())
        follow = []
        for call in tool_calls:
            if self.api_generation != generation:
                self.api_tool_running = False
                return
            function = call.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args if isinstance(raw_args, dict) else {})
            except ValueError:
                output = "工具参数不是合法 JSON。"
            else:
                label = tool_label(name, args)
                self.chat.append("工具", label)
                self.chat.status.setText(label)
                self.set_mood("working")
                if blocked and name != "fetch_url":
                    output = "当前窗口还没有解析出目录，已拒绝改用主目录。"
                else:
                    output = run_tool(name, args, root, lambda title, detail: confirm_action(title, detail, self.chat))
            follow.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": output})
        self.api_tool_running = False
        if self.api_generation != generation:
            return
        self.post_api(self.api_messages + [assistant] + follow)

    def stop_reply(self):
        self.pending_message = None
        if self.api_inflight or self.api_tool_running:
            self.cancel_api()
            self.on_finished("已停止")
            self.chat.status.setText("已停止")
        else:
            self.bridge.stop()

    def on_error(self, message):
        self.chat.status.setText(message)
        if self.chat.isVisible():
            self.chat.append("错误", message)
        self.chat.streaming = False
        self.chat.send_button.setEnabled(True)
        self.chat.stop_button.hide()
        self.set_mood("idle")

    def on_status(self, message):
        self.chat.status.setText(message)
        if "思考" in message: self.set_mood("thinking")
        elif "回复" in message or "执行" in message: self.set_mood("working")

    def on_finished(self, text):
        self.chat.finish(text)
        self.set_mood("done")
        QTimer.singleShot(1500, lambda: self.set_mood("idle") if self.chat.send_button.isEnabled() else None)

    def open_settings(self):
        dialog = SettingsDialog(self, self.settings)
        if dialog.exec_() != QDialog.Accepted:
            return
        cwd = str(Path(dialog.cwd.text()).expanduser().resolve())
        if not Path(cwd).is_dir():
            QMessageBox.warning(self, "Orbit", "请选择存在的工作目录。")
            return
        self.settings = {"cwd": cwd, "attach": dialog.auto_attach.isChecked(),
                         "auto_open": dialog.auto_open.isChecked(), "hints": dialog.hints.isChecked(),
                         "provider": "api" if dialog.provider.currentIndex() else "codex",
                         "endpoint": dialog.endpoint.text().strip(), "model": dialog.model.text().strip()}
        self.api_token = dialog.token.text().strip()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2))
        self.set_autostart(dialog.autostart.isChecked())

    def set_autostart(self, enabled):
        target = autostart_file()
        if enabled:
            desktop = APP_DIR / "orbit-codex.desktop"
            if not desktop.exists():
                QMessageBox.information(self, "Orbit", "请先运行 install.sh 安装启动项。")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(desktop.read_text())
        else:
            target.unlink(missing_ok=True)


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
