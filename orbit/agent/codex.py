"""Talk to the local Codex CLI over its app-server protocol."""

import json
import os
import shutil
import time
from pathlib import Path

from PyQt5.QtCore import QProcess, QProcessEnvironment, QTimer, pyqtSignal
from PyQt5.QtWidgets import QWidget

from orbit.config import get_config
from orbit.desktop.context import context_prompt
from orbit.ui.confirm import approval_summary, confirm_action


def find_codex():
    found = shutil.which("codex")
    if found:
        return found
    if os.name == "nt":
        found = shutil.which("codex.cmd")
        if found:
            return found
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path.home() / "AppData" / "Roaming" / "npm" / "codex.cmd",
            Path(local) / "Programs" / "codex" / "codex.exe" if local else None,
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                return str(candidate)
        return None
    candidates = sorted((Path.home() / ".nvm" / "versions" / "node").glob("*/bin/codex"))
    return str(candidates[-1]) if candidates else None

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
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PATH", str(Path(codex).parent) + os.pathsep + environment.value("PATH"))
        self.process.setProcessEnvironment(environment)
        self.stderr_tail = ""
        self.buffer = b""
        self.callbacks.clear()
        if os.name == "nt" and codex.lower().endswith((".cmd", ".bat")):
            self.process.setProgram(os.environ.get("COMSPEC", "cmd.exe"))
            self.process.setArguments(["/d", "/c", codex, "app-server", "--listen", "stdio://"])
        else:
            self.process.setProgram(codex)
            self.process.setArguments(["app-server", "--listen", "stdio://"])
        self.process.setWorkingDirectory(str(Path.home()))
        self.process.start()
        self.send("initialize", {"clientInfo": {"name": get_config().codex.client_name, "title": get_config().codex.client_title, "version": get_config().app.version}}, self.initialized)

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
        self.watchdog.start(get_config().codex.watchdog_ms)
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
            self.watchdog.start(get_config().codex.watchdog_ms)
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
