"""Task-scoped approval and a short undo stack for file writes."""

import os
from pathlib import Path


def same_place(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


class TaskGrants:
    """Skip repeated prompts for files or commands in one directory."""

    def __init__(self):
        self.file_root = None
        self.shell_root = None

    def clear(self):
        self.file_root = None
        self.shell_root = None

    def allow_files(self, root):
        self.file_root = os.path.normcase(os.path.normpath(str(root)))

    def allow_shell(self, root):
        self.shell_root = os.path.normcase(os.path.normpath(str(root)))

    def files_ok(self, root):
        return same_place(self.file_root, root)

    def shell_ok(self, root):
        return same_place(self.shell_root, root)

    def active(self):
        return bool(self.file_root or self.shell_root)


class FileUndo:
    """Keep the previous bytes of files Orbit itself writes."""

    def __init__(self, limit=20, max_bytes=1_500_000):
        self.stack = []
        self.limit = limit
        self.max_bytes = max_bytes

    def clear(self):
        self.stack.clear()

    def remember(self, path):
        target = Path(path)
        try:
            if target.exists():
                data = target.read_bytes()
                if len(data) > self.max_bytes:
                    return "large"
            else:
                data = None
        except OSError as error:
            return str(error)
        self.stack.append((str(target), data))
        del self.stack[:-self.limit]
        return "ok"

    def undo(self):
        if not self.stack:
            return "没有可以撤销的文件修改。"
        path, data = self.stack.pop()
        target = Path(path)
        try:
            if data is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        except OSError as error:
            self.stack.append((path, data))
            return f"撤销失败：{error}"
        return f"已撤销对 {target.name} 的修改。"


def approval_paths(params):
    """Pull file paths out of a Codex approval payload."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in {"path", "filepath", "file_path", "file"} and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(params or {})
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique
