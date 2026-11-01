"""File and shell tools offered to a Chat Completions model."""

import json
import os
import re
import signal
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from orbit.config import get_config, shell_timeout_seconds
from orbit.desktop.files import (
    SKIP_DIRS, clip_text, list_directory, looks_secret, read_file_text, scoped_path, search_files,
)

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

def html_to_text(raw):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def fetch_page(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "只允许抓取 http 或 https 地址。"
    request = urllib.request.Request(url, headers={"User-Agent": "Orbit/" + get_config().app.version})
    try:
        with urllib.request.urlopen(request, timeout=get_config().tools.fetch_timeout) as response:
            payload = response.read(150_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception as error:
        return "抓取失败：" + api_error_text(error)
    return clip_text(html_to_text(payload.decode(charset, "replace")), 8000)


_shell_lock = threading.Lock()
_shell_pgid = None


def kill_running_shell():
    with _shell_lock:
        pgid = _shell_pgid
    if not pgid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pgid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_shell_command(command, root):
    """Run a shell command without leaving git or curl alive after the timeout."""
    global _shell_pgid
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    process = subprocess.Popen(
        command, shell=True, cwd=str(root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    with _shell_lock:
        _shell_pgid = process.pid
    try:
        timeout = shell_timeout_seconds()
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_running_shell()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
        return f"命令超过 {timeout} 秒，已中止。"
    finally:
        with _shell_lock:
            if _shell_pgid == process.pid:
                _shell_pgid = None
    output = (stdout or "") + (("\n" + stderr) if stderr else "")
    return clip_text(f"退出码 {process.returncode}\n{output}".strip())


def _replace_failure(old, count):
    preview = (old or "").strip().splitlines()
    head = preview[0][:80] if preview else ""
    if not old:
        return "没有给出要替换的原文。"
    if count == 0:
        shown = f"原文开头是「{head}」。" if head else ""
        return "没有找到这段原文，所以没有改文件。" + shown + "请按文件里现在的内容，重新给一段完整且只出现一次的原文。"
    return f"这段原文出现了 {count} 次，没法确定改哪一处。请多带上前后几行，让它只出现一次。"


def run_tool(name, args, root, confirm, undo=None):
    args = args or {}
    try:
        if name == "list_dir":
            path = scoped_path(root, args.get("path") or ".")
            if not path.is_dir():
                return "不是目录：" + str(path)
            entries = list_directory(path, get_config().tools.list_limit)
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
                return _replace_failure(old, count)
            preview = f"{path}\n\n--- 原文 ---\n{old[:4000]}\n\n--- 改为 ---\n{new[:4000]}"
            if not confirm(f"修改 {path.name}", preview):
                return "用户拒绝了这次修改。"
            noted = undo(path) if undo else "ok"
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            extra = "（文件较大，这次不能撤销）" if noted == "large" else ""
            return "已写入 " + str(path) + extra
        if name == "write_file":
            path = scoped_path(root, args.get("path"))
            if looks_secret(path):
                return "敏感文件，已拒绝写入：" + path.name
            content = str(args.get("content") if args.get("content") is not None else "")
            if len(content) > 200_000:
                return "内容过长，已拒绝写入。"
            action = "覆盖" if path.exists() else "新建"
            if not confirm(f"{action} {path.name}", f"路径：{path}\n\n{content[:8000]}"):
                return "用户拒绝了这次写入。"
            noted = undo(path) if undo else "ok"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            extra = "（文件较大，这次不能撤销）" if noted == "large" else ""
            return "已写入 " + str(path) + extra
        if name == "shell":
            command = str(args.get("command") or "").strip()
            if not command:
                return "缺少命令。"
            if not confirm("运行命令", f"命令：{command}\n目录：{root}"):
                return "用户拒绝了这条命令。"
            try:
                return run_shell_command(command, root)
            except OSError as error:
                return "命令失败：" + str(error)
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
