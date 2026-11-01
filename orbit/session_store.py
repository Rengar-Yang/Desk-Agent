"""Remember conversations per window, and one for the idle perch."""

import json
import os
import time

from orbit.config import CONFIG_DIR

SESSION_FILE = CONFIG_DIR / "session.json"
_MAX_MESSAGES = 80
_MAX_CHARS = 12000
_MAX_WINDOWS = 40


def _clip(text):
    text = text if isinstance(text, str) else ""
    if len(text) <= _MAX_CHARS:
        return text
    return text[:_MAX_CHARS] + "\n…（已截断）"


def _messages(items):
    found = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        text = item.get("text")
        if isinstance(author, str) and isinstance(text, str) and text.strip():
            found.append({"author": author, "text": _clip(text)})
    return found[-_MAX_MESSAGES:]


def _history(items):
    found = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            found.append({"role": role, "content": _clip(content)})
    return found[-40:]


def _record(messages, api_history):
    return {
        "messages": _messages(messages),
        "api_history": _history(api_history),
        "saved_at": time.time(),
    }


def _empty_store():
    return {"idle": _record([], []), "windows": {}}


def window_keys(window):
    """Stable lookup keys. The live handle comes first, then path and title."""
    if not window:
        return []
    keys = []
    wid = window.get("id")
    if wid:
        keys.append(f"hwnd:{wid}")
    kind = window.get("kind") or "other"
    path = window.get("path") or ""
    if path:
        keys.append("path:" + kind + ":" + os.path.normcase(os.path.normpath(str(path))))
    app = (window.get("app") or "").strip().lower()
    title = (window.get("title") or "").strip()
    if app or title:
        keys.append(f"win:{kind}:{app}:{title}")
    return keys


def load_store():
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    if "windows" not in data and "messages" in data:
        return {"idle": _record(data.get("messages"), data.get("api_history")), "windows": {}}
    idle = data.get("idle") if isinstance(data.get("idle"), dict) else {}
    windows = {}
    raw = data.get("windows") if isinstance(data.get("windows"), dict) else {}
    for key, item in raw.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            continue
        windows[key] = _record(item.get("messages"), item.get("api_history"))
        windows[key]["saved_at"] = item.get("saved_at") or 0
    return {"idle": _record(idle.get("messages"), idle.get("api_history")), "windows": windows}


def _write(store):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _trim(windows):
    if len(windows) <= _MAX_WINDOWS:
        return windows
    ranked = sorted(windows.items(), key=lambda item: item[1].get("saved_at") or 0)
    keep = dict(ranked[-_MAX_WINDOWS:])
    return keep


def save_idle(messages, api_history):
    store = load_store()
    store["idle"] = _record(messages, api_history)
    _write(store)


def save_window(window, messages, api_history):
    keys = window_keys(window)
    if not keys:
        save_idle(messages, api_history)
        return
    store = load_store()
    record = _record(messages, api_history)
    for key in keys:
        store["windows"][key] = record
    store["windows"] = _trim(store["windows"])
    _write(store)


def load_idle():
    return load_store()["idle"]


def recall_window(window):
    """Return the saved transcript for this window, preferring one the user spoke in."""
    store = load_store()["windows"]
    found = []
    for key in window_keys(window):
        record = store.get(key)
        if record and record.get("messages"):
            found.append(record)
    if not found:
        return None
    for record in found:
        if any(item.get("author") == "你" for item in record["messages"]):
            return record
    return found[0]
