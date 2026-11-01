"""The settings dialog. Login autostart is applied when the dialog is saved."""

import os
import subprocess
import threading
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from orbit.agent.api_client import ApiError, request_completion
from orbit.agent.codex import find_codex
from orbit.config import follow_interval, mascot_scale, shell_timeout_seconds
from orbit.desktop.autostart import login_autostart_enabled
from orbit.presets import API_PRESETS, match_preset, preset_by_id
from orbit.ui.chrome import ACCENT, attach_card_shadow, floating_card_qss
from orbit.ui.fonts import ui_font


class SettingsDialog(QDialog):
    tested = pyqtSignal(str)

    def __init__(self, parent, settings):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFont(ui_font(13))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Orbit 设置")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._fill_model = False
        self.tested.connect(self._show_test)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame()
        card.setObjectName("orbitCard")
        attach_card_shadow(card)
        outer.addWidget(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
        scroll.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setAutoFillBackground(False)
        root = QVBoxLayout(body)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)
        scroll.setWidget(body)
        card_layout.addWidget(scroll)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setMaximumHeight(max(520, screen.availableGeometry().height() - 48))

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("设置")
        title.setObjectName("orbitTitle")
        subtitle = QLabel("回复来源、工作目录与桌面行为")
        subtitle.setObjectName("orbitSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        close = QPushButton("×")
        close.setObjectName("orbitCloseButton")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.reject)
        header.addWidget(close, 0, Qt.AlignTop)
        root.addLayout(header)

        root.addWidget(self._section("回复来源"))
        self.source = QComboBox()
        self.source.addItem("本机 Codex", "codex")
        for preset in API_PRESETS:
            self.source.addItem(preset.label, preset.id)
        self.source.addItem("自定义接口", "custom")
        self.source.setCurrentIndex(max(0, self.source.findData(self._initial_source(settings))))
        self.source.currentIndexChanged.connect(self._sync_fields)
        root.addWidget(self._stack("使用哪个后端回答", self.source))

        self.preset_hint = QLabel("")
        self.preset_hint.setObjectName("orbitHint")
        self.preset_hint.setWordWrap(True)
        root.addWidget(self.preset_hint)

        self.token = QLineEdit(parent.api_token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("粘贴 API Key，仅保存在本机")
        self.reveal = QPushButton("显示")
        self.reveal.setObjectName("orbitGhostButton")
        self.reveal.setCursor(Qt.PointingHandCursor)
        self.reveal.clicked.connect(self.toggle_token)
        token_row = QWidget()
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.setSpacing(8)
        token_layout.addWidget(self.token, 1)
        token_layout.addWidget(self.reveal)
        self.token_block = self._stack("API Key", token_row)
        root.addWidget(self.token_block)

        self.endpoint = QLineEdit(settings.get("endpoint", ""))
        self.endpoint.setPlaceholderText("https://你的服务/v1/chat/completions")
        self.endpoint_block = self._stack("完整 API 地址", self.endpoint)
        root.addWidget(self.endpoint_block)

        self.model = QLineEdit(settings.get("model", ""))
        self.model.setPlaceholderText("例如 gpt-4.1，预设也可以改成别的模型")
        self.model_block = self._stack("模型名称", self.model)
        root.addWidget(self.model_block)

        self.token_label = self.token_block.findChild(QLabel)
        self.endpoint_label = self.endpoint_block.findChild(QLabel)
        self.model_label = self.model_block.findChild(QLabel)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.test_button.setObjectName("orbitGhostButton")
        self.test_button.setCursor(Qt.PointingHandCursor)
        self.test_button.clicked.connect(self.test_connection)
        test_row.addWidget(self.test_button, 0, Qt.AlignLeft)
        test_row.addStretch()
        root.addLayout(test_row)

        root.addWidget(self._section("工作目录"))
        self.cwd = QLineEdit(settings.get("cwd", str(Path.home())))
        browse = QPushButton("浏览")
        browse.setObjectName("orbitGhostButton")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self.browse)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)
        path_layout.addWidget(self.cwd, 1)
        path_layout.addWidget(browse)
        root.addWidget(self._stack("默认目录（未吸附窗口时使用）", path_row))

        root.addWidget(self._section("桌面行为"))
        self.auto_attach = QCheckBox("拖到窗口后吸附")
        self.auto_attach.setChecked(settings.get("attach", True))
        self.auto_open = QCheckBox("吸附后自动打开对话")
        self.auto_open.setChecked(settings.get("auto_open", True))
        self.hints = QCheckBox("拖动时显示目标提示")
        self.hints.setChecked(settings.get("hints", True))
        self.autostart = QCheckBox("登录桌面后自动启动")
        self.autostart.setChecked(login_autostart_enabled())
        for box in (self.auto_attach, self.auto_open, self.hints, self.autostart):
            box.setCursor(Qt.PointingHandCursor)
            root.addWidget(box)

        scale = mascot_scale(settings)
        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(35, 300)
        self.scale.setValue(int(round(scale * 100)))
        self.scale_label = QLabel()
        self.scale.valueChanged.connect(self._show_scale)
        self._show_scale(self.scale.value())
        root.addWidget(self._stack(self.scale_label, self.scale))

        follow = follow_interval(settings)
        self.follow = QSlider(Qt.Horizontal)
        self.follow.setRange(40, 800)
        self.follow.setValue(follow)
        self.follow_label = QLabel()
        self.follow.valueChanged.connect(self._show_follow)
        self._show_follow(self.follow.value())
        root.addWidget(self._stack(self.follow_label, self.follow))

        self.shell_timeout = QSpinBox()
        self.shell_timeout.setRange(5, 300)
        self.shell_timeout.setSuffix(" 秒")
        self.shell_timeout.setValue(shell_timeout_seconds(settings))
        root.addWidget(self._stack("命令最长运行时间", self.shell_timeout))

        note = QLabel("选好预设后填写 API Key，模型名可以改。吸附窗口后会标明实际读到了什么。写入和命令可以对本目录选择本次允许。Windows 上按 Ctrl+Alt+Space 唤出对话。")
        note.setObjectName("orbitNote")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("orbitGhostButton")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("orbitPrimaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.accept)
        save.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        self.setStyleSheet(floating_card_qss(ACCENT))
        self._sync_fields()
        self._fill_model = True

    @staticmethod
    def _section(text):
        label = QLabel(text.upper())
        label.setObjectName("orbitSection")
        return label

    def _stack(self, caption, widget):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if isinstance(caption, QLabel):
            label = caption
        else:
            label = QLabel(caption)
        label.setObjectName("orbitFieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return block

    def _show_scale(self, value):
        self.scale_label.setText(f"吉祥物大小  {value / 100:.2f} 倍")

    def _show_follow(self, value):
        self.follow_label.setText(f"吸附跟随间隔  {value} 毫秒")

    def toggle_token(self):
        hidden = self.token.echoMode() == QLineEdit.Password
        self.token.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        self.reveal.setText("隐藏" if hidden else "显示")

    @staticmethod
    def _initial_source(settings):
        if settings.get("provider") != "api":
            return "codex"
        preset_id = settings.get("preset") or ""
        if preset_id == "custom" or preset_by_id(preset_id):
            return preset_id or "custom"
        matched = match_preset(settings.get("endpoint"), settings.get("model"))
        return matched.id if matched else "custom"

    def _sync_fields(self):
        kind = self.source.currentData()
        is_codex = kind == "codex"
        is_custom = kind == "custom"
        self.token_block.setVisible(not is_codex)
        self.endpoint_block.setVisible(is_custom)
        self.model_block.setVisible(not is_codex)
        preset = None if is_codex or is_custom else preset_by_id(kind)
        self.preset_hint.setVisible(True)
        if preset is not None:
            if self._fill_model or not self.model.text().strip():
                self.model.setText(preset.model)
            self.preset_hint.setText(f"将使用 {preset.label}。地址已预设，模型名可以改成同账号下的其他模型。")
        elif is_codex:
            self.preset_hint.setText("使用本机已登录的 Codex。测试连接会检查能不能找到这条命令。")
        else:
            self.preset_hint.setText("填写完整的 Chat Completions 地址、模型名和 API Key。")

    def choice(self):
        """Return provider, preset id, endpoint, and model for the current selection."""
        kind = self.source.currentData()
        if kind == "codex":
            return {"provider": "codex", "preset": "", "endpoint": "", "model": ""}
        if kind == "custom":
            return {
                "provider": "api",
                "preset": "custom",
                "endpoint": self.endpoint.text().strip(),
                "model": self.model.text().strip(),
            }
        preset = preset_by_id(kind)
        model = self.model.text().strip() or (preset.model if preset else "")
        return {"provider": "api", "preset": preset.id, "endpoint": preset.endpoint, "model": model}

    def test_connection(self):
        self.test_button.setEnabled(False)
        self.preset_hint.setVisible(True)
        self.preset_hint.setText("正在测试连接…")
        chosen = self.choice()
        token = self.token.text().strip()
        threading.Thread(target=self._run_test, args=(chosen, token), daemon=True).start()

    def _run_test(self, chosen, token):
        try:
            text = self._probe(chosen, token)
        except Exception as error:
            text = "测试失败：" + str(error)
        self.tested.emit(text)

    @staticmethod
    def _probe(chosen, token):
        if chosen["provider"] != "api":
            path = find_codex()
            if not path:
                return "没有找到 codex 命令。请先安装并登录 Codex CLI，或改用上面的 API。"
            if path.lower().endswith((".cmd", ".bat")):
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", path, "--version"]
            else:
                command = [path, "--version"]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=12)
            except (OSError, subprocess.TimeoutExpired) as error:
                return "找到了 Codex，但没有跑起来：" + str(error)
            version = (completed.stdout or completed.stderr or "").strip().splitlines()
            shown = version[0][:120] if version else "已找到命令"
            if completed.returncode != 0:
                return f"Codex 已找到，但版本检查返回 {completed.returncode}。{shown}"
            return "已找到本机 Codex：" + shown
        endpoint = chosen["endpoint"]
        model = chosen["model"]
        if not endpoint or not model:
            return "请先填写 API 地址和模型名称。"
        if not token:
            return "请先填写 API Key。"
        try:
            message = request_completion(
                endpoint, token,
                {"model": model, "messages": [{"role": "user", "content": "只回复一个字：好"}], "max_tokens": 16},
                25,
            )
        except ApiError as error:
            return "连接失败：" + str(error)
        reply = message.get("content") if isinstance(message, dict) else ""
        reply = reply.strip() if isinstance(reply, str) else ""
        return "连接成功。" + (f"模型回复：{reply[:40]}" if reply else "接口已返回。")

    def _show_test(self, text):
        self.test_button.setEnabled(True)
        self.preset_hint.setVisible(True)
        self.preset_hint.setText(text)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", self.cwd.text())
        if path:
            self.cwd.setText(path)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
