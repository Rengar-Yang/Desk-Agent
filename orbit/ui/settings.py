"""The settings dialog. Login autostart is applied when the dialog is saved."""

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

from orbit.desktop.autostart import login_autostart_enabled


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
        self.autostart.setChecked(login_autostart_enabled())
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
