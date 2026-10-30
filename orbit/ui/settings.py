"""The settings dialog. Login autostart is applied when the dialog is saved."""

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

from orbit.desktop.autostart import login_autostart_enabled
from orbit.presets import API_PRESETS, match_preset, preset_by_id
from orbit.ui.fonts import ui_font


class SettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setFont(ui_font(13))
        self.setWindowTitle("Orbit 设置")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.source = QComboBox()
        self.source.addItem("本机 Codex", "codex")
        for preset in API_PRESETS:
            self.source.addItem(preset.label, preset.id)
        self.source.addItem("自定义接口", "custom")
        self.source.setCurrentIndex(max(0, self.source.findData(self._initial_source(settings))))
        self.source.currentIndexChanged.connect(self._sync_fields)
        form.addRow("回复来源", self.source)

        self.preset_hint = QLabel("")
        self.preset_hint.setWordWrap(True)
        form.addRow(self.preset_hint)

        self.token_label = QLabel("API Key")
        self.token = QLineEdit(parent.api_token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("粘贴 API Key，保存后只留在本机")
        form.addRow(self.token_label, self.token)

        self.endpoint_label = QLabel("完整 API 地址")
        self.endpoint = QLineEdit(settings.get("endpoint", ""))
        self.endpoint.setPlaceholderText("https://你的服务/v1/chat/completions")
        form.addRow(self.endpoint_label, self.endpoint)

        self.model_label = QLabel("模型名称")
        self.model = QLineEdit(settings.get("model", ""))
        self.model.setPlaceholderText("例如 gpt-4.1")
        form.addRow(self.model_label, self.model)

        self.cwd = QLineEdit(settings.get("cwd", str(Path.home())))
        browse = QPushButton("选择…")
        browse.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.cwd)
        row.addWidget(browse)
        form.addRow("工作目录", row)
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
        note = QLabel("选好 DeepSeek、GPT 等预设后，只需填写 API Key。吸附后会把工作目录交给当前回复来源；写入文件和运行命令会先确认。")
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
        self._sync_fields()

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
        self.token_label.setVisible(not is_codex)
        self.token.setVisible(not is_codex)
        self.endpoint_label.setVisible(is_custom)
        self.endpoint.setVisible(is_custom)
        self.model_label.setVisible(is_custom)
        self.model.setVisible(is_custom)
        preset = None if is_codex or is_custom else preset_by_id(kind)
        self.preset_hint.setVisible(preset is not None)
        if preset is not None:
            self.preset_hint.setText(f"将使用 {preset.label} 的 {preset.model}。地址已预设，只需填写 API Key。")

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
        return {"provider": "api", "preset": preset.id, "endpoint": preset.endpoint, "model": preset.model}

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", self.cwd.text())
        if path:
            self.cwd.setText(path)
