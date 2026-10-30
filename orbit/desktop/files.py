"""Paths, directory listings, and text that is safe to show the model."""

import os
import re
import shutil
import subprocess
from pathlib import Path

SECRET_NAME = re.compile(r"(?i)(^api\.txt$|(^|\.)env($|\.)|credential|secret|token|password|id_rsa|\.pem$|cookies)")
SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".cache"}
PLACE_NAMES = {
    "home": "home", "主文件夹": "home", "主目录": "home",
    "desktop": "desktop", "桌面": "desktop",
    "documents": "documents", "文档": "documents",
    "downloads": "downloads", "下载": "downloads",
    "music": "music", "音乐": "music",
    "pictures": "pictures", "图片": "pictures",
    "videos": "videos", "视频": "videos", "movies": "videos",
}

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

def unique_directory_named(name):
    if not name or len(name) > 180 or "/" in name or "\\" in name or ":" in name:
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
