"""File and shell tools offered to a Chat Completions model."""

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from orbit.config import get_config, shell_timeout_seconds
from orbit.desktop.docx import edit_docx, normalize_format, read_docx_paragraphs
from orbit.desktop.files import (
    SKIP_DIRS, clip_text, list_directory, looks_secret, read_file_text, scoped_path, search_files,
)
from orbit.desktop.office import edit_open_document, open_document_path, read_open_document

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
        "name": "read_document",
        "description": "读取吸附的 WPS/Word .docx。返回带序号的段落，优先读正在打开的那一份。path 可省略。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文档路径，可省略"},
            "limit": {"type": "integer", "description": "最多返回多少段，默认 120，最大 400"},
        }},
    }},
    {"type": "function", "function": {
        "name": "edit_document",
        "description": "直接修改 .docx 的文字和格式，调用后立即写入，不要先询问。mode 默认 replace：paragraph+new 替换整段；再加 old 只改几个字；只有 old+new 时原文必须只出现一次。mode=replace_all 全部替换。mode=insert 插入，paragraph 是插在第几段后，0 是文首，省略是文末。mode=delete 删除段落。mode=format 只改格式不改文字。format 可含 bold、italic、underline、strike、font、size（磅或小四）、color（FF0000 或红）、highlight（yellow/green/red/none）、align（left/center/right/justify）、style（normal/title/heading1/heading2/heading3）。替换或插入时 format 套在新文字上。文档正打开时改 WPS/Word 里的那一份并保存。不要用 shell 或 python。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文档路径，可省略，默认当前吸附的文档"},
            "mode": {"type": "string", "description": "replace、replace_all、insert、delete、format，默认 replace"},
            "paragraph": {"type": "integer", "description": "段落号，从 1 开始，与 read_document 一致。insert 时 0 表示文首，省略表示文末"},
            "old": {"type": "string", "description": "要替换或套格式的原文，不要带段落序号"},
            "new": {"type": "string", "description": "替换或插入的文字。delete 和 format 时不用填"},
            "format": {"type": "object", "description": "格式。bold/italic/underline/strike、font、size、color、highlight、align、style"},
        }},
    }},
    {"type": "function", "function": {
        "name": "shell",
        "description": "在当前工作目录运行一条 shell 命令。用户确认后才会执行。不要用它读取或修改 .docx，那会没有输出；改文档用 read_document 和 edit_document。",
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


_WORD_SCRIPT = re.compile(r"document\.xml|python-docx|<w:t>|word/document|zipfile", re.I)
_PYTHON = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}


def _python_stub(command):
    token = (command or "").strip().split(None, 1)
    if not token:
        return False
    name = Path(token[0].strip('"')).name.lower()
    if name not in _PYTHON:
        return False
    found = shutil.which(name)
    return not found or "windowsapps" in found.lower()


def _shell_result(code, stdout, stderr):
    out = stdout or ""
    err = stderr or ""
    if not out.strip() and not err.strip():
        note = "标准输出和标准错误都是空的。退出码只说明进程结束了，不能说明文件已经被修改。"
        return f"退出码 {code}\n{note}"
    return clip_text(f"退出码 {code}\n{out}{chr(10) + err if err else ''}".strip())


def run_shell_command(command, root):
    """Run a shell command without leaving git or curl alive after the timeout."""
    global _shell_pgid
    if _WORD_SCRIPT.search(command or ""):
        return (
            "没有执行这条命令。用 python 或压缩包脚本改 Word 时，经常是退出码 0 且没有任何输出，"
            "这不能说明文档已修改。请改用 read_document 看段落序号，再用 edit_document。"
            "改整段传 paragraph 和 new；只改几个字再加 old；插入用 mode=insert；删除用 mode=delete；全部替换用 mode=replace_all；改格式用 mode=format。"
        )
    if _python_stub(command):
        return (
            "没有执行。这里的 python 命令不是可用解释器，它会直接退出且不产生输出。"
            "读取和修改 Word 请用 read_document 和 edit_document。"
        )
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        command, shell=True, cwd=str(root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
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
    return _shell_result(process.returncode, stdout, stderr)


_UNVERIFIED_EDIT = (
    "已写入",
    "已改好",
    "已把第",
    "整段替换",
    "已替换为",
    "命令已执行",
    "诊断命令已执行",
    "已经改成",
    "修改已完成",
)


def claims_unverified_edit(text):
    """True when a reply says a document or command already finished."""
    body = text or ""
    return any(piece in body for piece in _UNVERIFIED_EDIT)


def edit_succeeded(text):
    return (text or "").startswith(("已改好", "已写入", "已直接改好"))


def _replace_failure(old, count):
    preview = (old or "").strip().splitlines()
    head = preview[0][:80] if preview else ""
    if not old:
        return "没有给出要替换的原文。"
    if count == 0:
        shown = f"原文开头是「{head}」。" if head else ""
        return "没有找到这段原文，所以没有改文件。" + shown + "请按文件里现在的内容，重新给一段完整且只出现一次的原文。"
    return f"这段原文出现了 {count} 次，没法确定改哪一处。请多带上前后几个字，或改用 mode=replace_all 全部替换。"


def _document_file(args, root, attached):
    return open_document_path(args.get("path"), root, attached)


def _read_document(args, root, attached):
    path = _document_file(args, root, attached)
    if path.suffix.lower() not in {".docx", ".docm"}:
        return "目前只能直接读取 .docx 的正文。文件在：" + str(path)
    limit = _paragraph_limit(args.get("limit"))
    live = read_open_document(path, limit)
    if live:
        return clip_text(f"{path}\n{live}\n（这是正在打开的 WPS/Word）\n{_DOC_HINT}", 20000)
    try:
        text = read_docx_paragraphs(path, limit)
    except OSError:
        text = ""
    except (KeyError, ValueError, SyntaxError):
        return "这个文件不是有效的 docx。"
    except Exception as error:
        name = type(error).__name__
        if name in {"BadZipFile", "ParseError"}:
            return "这个文件不是有效的 docx。"
        return "读取失败：" + str(error)
    if text:
        return clip_text(f"{path}\n{text}\n{_DOC_HINT}", 20000)
    return "没能读到这份文档的正文。请确认它是 .docx，并且 WPS 里正打开这一份。"


def _paragraph_limit(value):
    try:
        return max(1, min(400, int(value or 120)))
    except (TypeError, ValueError):
        return 120


_DOC_HINT = (
    "改整段：paragraph + new。只改段内几个字：再加上 old。"
    "在第 N 段后插入：mode=insert、paragraph=N（0 是文首，省略是文末）。"
    "删段：mode=delete。全部替换：mode=replace_all。"
    "只改格式：mode=format，并用 format 指定 bold、font、size、color、highlight、align、style。"
    "方括号里是当前格式，不是正文。换行写成 \\n。"
)


def _edit_mode(args):
    mode = str(args.get("mode") or "replace").strip().lower()
    return {
        "append": "insert", "add": "insert", "remove": "delete", "all": "replace_all",
        "format": "format", "格式": "format",
    }.get(mode, mode)


def _edit_index(args):
    paragraph = args.get("paragraph")
    if paragraph in (None, ""):
        return None
    return int(paragraph)


def _arm_undo(undo, path):
    if not undo:
        return False
    try:
        return undo(path) == "ok"
    except OSError:
        return False


def _drop_undo(undo, armed):
    if not armed:
        return
    owner = getattr(undo, "__self__", None)
    discard = getattr(owner, "discard", None)
    if discard:
        discard()


def _collect_format(args):
    raw = {}
    nested = args.get("format")
    if isinstance(nested, dict):
        raw.update(nested)
    for key in (
        "bold", "italic", "underline", "strike", "font", "size", "color", "highlight", "align", "style",
        "加粗", "斜体", "下划线", "删除线", "字体", "字号", "颜色", "高亮", "对齐", "样式",
    ):
        if key in args and args.get(key) not in (None, ""):
            raw.setdefault(key, args.get(key))
    return normalize_format(raw)


def _with_format_note(message, fmt, mode):
    if not fmt or mode == "format" or message.endswith("已套用格式。"):
        return message
    if message.endswith("。"):
        return message[:-1] + "，已套用格式。"
    return message + "已套用格式。"


def _live_action(mode, index):
    if mode in {"insert", "delete", "replace_all", "format"}:
        return mode
    if index is not None:
        return "paragraph"
    return "replace"


def _where(index):
    if index is None:
        return "文末"
    if index == 0:
        return "文首"
    return f"第 {index} 段后面"


def _live_success(path, mode, index, result):
    count = result.get("count") or 1
    if mode == "format":
        target = f"第 {index} 段" if index else "匹配的文字"
        return f"已直接改好：已为{target}套用格式。正在打开的文档是 {path}。"
    if mode == "delete":
        return f"已直接改好：已删除第 {index} 段。正在打开的文档是 {path}。"
    if mode == "insert":
        return f"已直接改好：已在{_where(index)}插入文字。正在打开的文档是 {path}。"
    if mode == "replace_all":
        return f"已直接改好正在打开的文档：{path}。替换了 {count} 处。"
    if index:
        return f"已直接改好第 {index} 段，正在打开的文档是 {path}。"
    return f"已直接改好正在打开的文档：{path}。"


def _disk_success(path, mode, index, count):
    tail = "如果 WPS 还显示旧内容，关掉后重新打开即可看到修改。"
    if mode == "format":
        target = f"第 {index} 段" if index else "匹配的文字"
        return f"已写入，已为{target}套用格式：{path}。{tail}"
    if mode == "delete":
        return f"已写入，已删除第 {index} 段：{path}。{tail}"
    if mode == "insert":
        return f"已写入新段落到{_where(index)}：{path}。{tail}"
    if mode == "replace_all":
        return f"已写入 {path}。替换了 {count} 处。{tail}"
    if index:
        return f"已写入第 {index} 段：{path}。{tail}"
    return f"已写入 {path}。{tail}"


def _live_failure(status, result, index):
    if status == "badindex":
        total = result.get("count")
        if total:
            return f"没有第 {index} 段。这份文档共 {total} 段。请先用 read_document 看序号。"
        return f"没有第 {index} 段。请先用 read_document 看序号。"
    if status == "ambiguous":
        count = result.get("count") or 0
        place = f"第 {index} 段里" if index else "文档里"
        return f"{place}这段原文出现了 {count} 次，没有写入。请把 old 写得更完整，或改用 mode=replace_all。"
    if status == "nomatch":
        return "打开的文档里没有这段原文，没有写入。请按屏幕上现在的文字重新给 old。"
    if status == "error":
        return "WPS/Word 没有完成修改：" + str(result.get("message") or "未知错误")
    return None


def _disk_failure(code, info, index, old):
    if code == "badindex":
        total = info if isinstance(info, int) else 0
        if total:
            return f"没有第 {index} 段。这份文档共 {total} 段。请先用 read_document 看序号。"
        return f"没有第 {index} 段。请先用 read_document 看序号。"
    if code == "ambiguous":
        place = f"第 {index} 段里" if index else "文档里"
        return f"{place}这段原文出现了 {info} 次，没有写入。请把 old 写得更完整，或改用 mode=replace_all。"
    if code == "missing":
        return _replace_failure(old, 0) if old else "没有写进文档。"
    return "没有写进文档。"


def _edit_document(args, root, confirm, undo, attached):
    import zipfile
    from xml.etree.ElementTree import ParseError

    path = _document_file(args, root, attached)
    if path.suffix.lower() not in {".docx", ".docm"}:
        return "目前只能直接修改 .docx。请在 WPS 里另存为 .docx 后再改。文件在：" + str(path)
    mode = _edit_mode(args)
    if mode not in {"replace", "replace_all", "insert", "delete", "format"}:
        return "mode 只能是 replace、replace_all、insert、delete 或 format。"
    try:
        index = _edit_index(args)
    except (TypeError, ValueError):
        return "paragraph 必须是整数。段落从 1 开始，插入时 0 表示文首。"
    new = str(args.get("new") if args.get("new") is not None else "")
    old = str(args.get("old") if args.get("old") is not None else "")
    if len(old) > 20_000 or len(new) > 20_000:
        return "替换内容过长。"
    fmt = _collect_format(args)
    if fmt and not new and mode in {"replace", "replace_all"}:
        mode = "format"
    if mode == "delete":
        if index is None or index < 1:
            return "删除时要给出 paragraph，也就是要删的段落号。"
    elif mode == "insert":
        if not new:
            return "插入时要给出 new，也就是新段落的文字。"
        if index is not None and index < 0:
            return "paragraph 不能是负数。0 表示插到文首。"
    elif mode == "replace_all":
        if not old:
            return "全部替换时要给出 old 和 new。"
    elif mode == "format":
        if not fmt:
            return "套用格式时要给出 format，例如加粗、字体、字号、颜色、对齐或标题样式。"
        if (index is None or index < 1) and not old:
            return "套用格式时要给出 paragraph，或给出 old 指明哪几个字。"
    else:
        if index is None and not old:
            return "没有给出要改的内容。可以传 paragraph 和 new 替换整段，或传 old 和 new 替换原文。"
        if index is not None and index < 1:
            return "paragraph 必须是从 1 开始的整数。"
    armed = _arm_undo(undo, path)
    live_index = index
    if mode == "insert" and index is None:
        live_index = None
    live = edit_open_document(path, _live_action(mode, index), old, new, live_index, fmt=fmt)
    status = live.get("status")
    if status == "replaced":
        return _with_format_note(_live_success(path, mode, index, live), fmt, mode)
    if status in {"nomatch", "ambiguous", "badindex", "error"}:
        _drop_undo(undo, armed)
        return _live_failure(status, live, index)
    try:
        code, info = edit_docx(path, mode, paragraph=index, old=old, new=new, fmt=fmt)
    except PermissionError:
        _drop_undo(undo, armed)
        return "文档正被 WPS 占用，这次没改到打开的内容。请保持该文档打开后再改一次。"
    except (KeyError, zipfile.BadZipFile, SyntaxError, ParseError, ValueError):
        _drop_undo(undo, armed)
        return "这个文件不是有效的 docx。"
    except OSError as error:
        _drop_undo(undo, armed)
        return "写入失败：" + str(error)
    if code != "ok":
        _drop_undo(undo, armed)
        return _disk_failure(code, info, index, old)
    return _with_format_note(_disk_success(path, mode, index, info), fmt, mode)


def run_tool(name, args, root, confirm, undo=None, document=None):
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
        if name == "read_document":
            return _read_document(args, root, document)
        if name == "edit_document":
            return _edit_document(args, root, confirm, undo, document)
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
        "read_document": "读取文档", "edit_document": "修改文档",
    }
    text = names.get(name, name)
    if name == "edit_document":
        mode = str(args.get("mode") or "replace")
        paragraph = args.get("paragraph")
        detail = {
            "replace": "替换", "replace_all": "全部替换", "insert": "插入", "delete": "删除", "format": "改格式",
        }.get(mode, mode)
        if paragraph not in (None, ""):
            detail += f" 第{paragraph}段"
        return text + " · " + detail
    return text + ((" · " + str(target)[:80]) if target else "")
