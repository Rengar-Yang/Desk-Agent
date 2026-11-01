"""Turn an attached window into a path, listing, and prompt."""

import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt5.QtCore import QRect

from orbit.config import get_config
from orbit.desktop.accessibility import accessibility_snapshot
from orbit.desktop.files import (
    SKIP_DIRS, list_directory, read_text_preview, resolve_path_buttons, unique_directory_named,
)
from orbit.desktop.common import classify_window
from orbit.theme import ACCENTS, KIND_LABELS

SHELL_NAMES = {"bash", "zsh", "fish", "sh", "dash", "cmd", "powershell", "pwsh"}

def _is_shell(comm):
    name = (comm or "").lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name in SHELL_NAMES


def _same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _windows_path(raw):
    if len(raw) >= 3 and raw[1] == ":" and raw[2] in ("\\", "/"):
        return True
    return raw.startswith("\\\\")


def parse_terminal_cwd(title):
    text = title.strip().strip('"')
    match = re.match(r"^[^@\s]+@[^:]+:\s*(.+)$", text)
    raw = match.group(1).strip() if match else text
    if not (raw.startswith("~") or raw.startswith("/") or _windows_path(raw)):
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def parse_editor_title(title):
    cleaned = re.sub(
        r"\s+[—–-]\s+(Cursor|Visual Studio Code|Code|VSCodium|Text Editor|gedit|Kate|Notepad\+\+|Notepad)$",
        "", title,
    ).strip()
    located = re.search(r"\((~?[/~][^)]+|[A-Za-z]:\\[^)]+)\)\s*$", cleaned)
    path = Path(located.group(1)).expanduser() if located and Path(located.group(1)).expanduser().exists() else None
    parts = [part.strip() for part in re.split(r"\s+[—–-]\s+", cleaned) if part.strip()]
    filename = parts[0] if parts else None
    workspace = parts[1] if len(parts) > 1 else None
    if filename and ("/" in filename or "\\" in filename):
        filename = Path(filename).name
    return filename, workspace, path


def file_uri_to_path(uri):
    if not uri:
        return None
    if uri.startswith("file:"):
        path = unquote(urlparse(uri).path)
        if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path)
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
    home = Path.home()
    bases = [
        home / ".config" / "Cursor" / "User" / "workspaceStorage",
        home / ".config" / "Code" / "User" / "workspaceStorage",
        home / ".config" / "Code - OSS" / "User" / "workspaceStorage",
    ]
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        bases.extend([
            appdata / "Cursor" / "User" / "workspaceStorage",
            appdata / "Code" / "User" / "workspaceStorage",
            appdata / "Code - OSS" / "User" / "workspaceStorage",
        ])
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
    if os.name == "nt":
        from orbit.desktop.win32 import windows_process_table
        return windows_process_table()
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
        if _is_shell(table[current]["comm"]) and table[current]["cwd"]:
            cwds.append(os.path.normcase(os.path.normpath(table[current]["cwd"])))
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
        if _is_shell(info["comm"]) and _same_path(info["cwd"], target):
            shells.append(pid)
        stack.extend(children.get(pid, []))
    lines = []
    for shell in shells:
        for pid in children.get(shell, []):
            info = table.get(pid)
            if not info or not _same_path(info["cwd"], target) or not info["cmd"]:
                continue
            text = re.sub(r"\s+", " ", info["cmd"])[:160]
            if text not in lines:
                lines.append(text)
            if len(lines) >= limit:
                return lines
    return lines

def capture_context(window):
    """Attach a path, listing, and bounded text preview to a window description."""
    kind = window.get("kind") or classify_window(window.get("app", ""))
    title = window.get("title") or "窗口"
    rect = window.get("rect") or (0, 0, 0, 0)
    if isinstance(rect, QRect):
        rect = (rect.x(), rect.y(), rect.width(), rect.height())
    if os.name != "nt" and kind in ("files", "browser", "editor"):
        snapshot = accessibility_snapshot(window.get("pid"), title, rect)
    else:
        snapshot = {}
    path = None
    entries = []
    preview = None
    detail = ""
    if kind == "files":
        folder = window.get("folder")
        path = Path(folder) if folder and Path(folder).is_dir() else None
        if path is None:
            path = resolve_path_buttons(snapshot.get("buttons") or [])
        if path is None:
            path = resolve_path_buttons([title])
        titled = unique_directory_named(title)
        if titled and (path is None or (path != titled and path in titled.parents)):
            path = titled
        if path:
            entries = list_directory(path, get_config().context.directory_limit)
            selected = snapshot.get("selection")
            selected_path = path / selected if selected and selected not in (path.name, title) else None
            if selected_path and selected_path.is_file():
                preview = read_text_preview(selected_path, get_config().context.selected_preview_chars)
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
            preview = read_text_preview(file_path, get_config().context.preview_chars)
            detail = "已读取文件开头" if preview else "文件不是文本或体积过大"
    elif kind == "browser":
        detail = snapshot.get("url") or "未暴露页面地址，可以依据标题协助"
        path = None
    if path is None and kind == "other":
        cwd = window.get("cwd")
        if cwd and Path(cwd) not in (Path.home(), Path("/")) and Path(cwd).is_dir():
            path = Path(cwd)
            entries = list_directory(path, get_config().context.directory_limit)
    summary = " · ".join(part for part in (str(path) if path else "", detail) if part)
    if kind == "files" and path:
        detail_level = "directory"
    elif kind == "editor" and path and preview:
        detail_level = "file"
    elif kind == "editor" and path:
        detail_level = "file_meta"
    elif kind == "terminal" and path:
        detail_level = "cwd"
    elif kind == "browser" and snapshot.get("url"):
        detail_level = "url"
    elif path:
        detail_level = "cwd"
    else:
        detail_level = "title"
    return {
        "kind": kind,
        "accent": ACCENTS[kind],
        "path": str(path) if path else None,
        "entries": entries,
        "preview": preview,
        "summary": summary or KIND_LABELS[kind],
        "url": snapshot.get("url"),
        "detail_level": detail_level,
    }


def context_prompt(window, cwd):
    if not window:
        return ""
    kind = KIND_LABELS.get(window.get("kind"), "窗口")
    lines = [
        "你正在协助用户操作一个已经吸附的桌面窗口。请直接使用下面的路径和内容，不要再向用户索取路径或要求粘贴文件。",
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
        lines.append("文本内容开头：\n" + preview[:get_config().context.preview_chars])
    elif window.get("kind") == "browser" and not window.get("url"):
        lines.append("浏览器没有暴露页面正文。若已知地址，请用工具抓取页面，不要只根据标题编造正文。")
    return "\n".join(lines) + "\n"
