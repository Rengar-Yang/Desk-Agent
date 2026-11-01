"""Ask before a tool or Codex action changes the machine."""

import json

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QCursor

from orbit.ui.fonts import mono_font_css, ui_font, ui_font_css
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)


def _kind_for(title, detail):
    text = f"{title}\n{detail or ''}"
    if "命令" in title or "命令：" in text:
        return "command"
    if any(word in title for word in ("修改", "覆盖", "新建", "写入", "文件")) or "--- 原文 ---" in text:
        return "file"
    return "permission"


def _parse_detail(detail):
    text = (detail or "").strip()
    fields = {"body": text}
    if "--- 原文 ---" in text and "--- 改为 ---" in text:
        head, rest = text.split("--- 原文 ---", 1)
        old, new = rest.split("--- 改为 ---", 1)
        fields.update(mode="edit", path=head.strip(), old=old.strip(), new=new.strip())
        return fields
    if text.startswith("路径："):
        first, _, rest = text.partition("\n")
        fields.update(mode="write", path=first[3:].strip(), body=rest.strip())
        return fields
    labeled = {}
    extra = []
    for line in text.splitlines():
        matched = False
        for prefix, key in (
            ("命令：", "command"), ("目录：", "cwd"), ("原因：", "reason"),
            ("写入范围：", "root"), ("权限：", "permissions"), ("路径：", "path"),
        ):
            if line.startswith(prefix):
                labeled[key] = line[len(prefix):].strip()
                matched = True
                break
        if not matched and line.strip():
            extra.append(line)
    if labeled:
        fields.update(labeled)
        if extra:
            fields["extra"] = "\n".join(extra)
        fields["mode"] = "fields"
    return fields


def _block(text, lines=4):
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(text or "")
    box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
    box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    count = min(12, max(lines, (text or "").count("\n") + 1))
    box.setFixedHeight(count * 18 + 18)
    return box


class ApprovalDialog(QDialog):
    """A card that shows exactly what will run or change, and refuses by default."""

    def __init__(self, title, detail, parent=None, allow_task=True):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFont(ui_font(13, subpixel=False))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setWindowTitle(title)
        kind = _kind_for(title, detail)
        accent = {"command": "#c56a3a", "file": "#5b6fbe", "permission": "#6d62b0"}[kind]
        label = {"command": "执行命令", "file": "修改文件", "permission": "额外权限"}[kind]
        soft = QColor(accent)
        soft.setAlpha(28)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        card = QFrame()
        card.setObjectName("approvalCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(40, 32, 70, 80))
        card.setGraphicsEffect(shadow)
        root.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        badge = QLabel(label)
        badge.setObjectName("approvalBadge")
        layout.addWidget(badge, 0, Qt.AlignLeft)
        heading = QLabel(title)
        heading.setObjectName("approvalTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.decision = "deny"
        if allow_task and kind in ("file", "command"):
            hint_text = "拒绝后这次操作不会执行。选「本次允许」后，同一目录里接下来的同类操作不再逐条询问，直到结束任务或清空对话。"
        else:
            hint_text = "拒绝后这次操作不会执行。"
        hint = QLabel(hint_text)
        hint.setObjectName("approvalHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._add_body(layout, _parse_detail(detail))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        deny = QPushButton("拒绝")
        deny.setObjectName("denyButton")
        allow = QPushButton("仅此一次")
        allow.setObjectName("allowButton")
        deny.clicked.connect(lambda: self._choose("deny"))
        allow.clicked.connect(lambda: self._choose("once"))
        deny.setAutoDefault(False)
        allow.setAutoDefault(False)
        allow.setDefault(False)
        buttons.addWidget(deny, 1)
        if allow_task and kind in ("file", "command"):
            task = QPushButton("本次允许")
            task.setObjectName("taskButton")
            task.clicked.connect(lambda: self._choose("task"))
            task.setAutoDefault(False)
            buttons.addWidget(task, 1)
        buttons.addWidget(allow, 1)
        layout.addLayout(buttons)
        keys = QLabel("Esc 拒绝    Ctrl+Enter 仅此一次")
        keys.setObjectName("approvalKeys")
        keys.setAlignment(Qt.AlignRight)
        layout.addWidget(keys)

        self.setStyleSheet(f"""
            #approvalCard {{ background: #fdfdff; border: 1px solid {accent}; border-radius: 18px; }}
            QLabel {{ color: #353448; background: transparent; font-family: {ui_font_css()}; font-size: 13px; }}
            #approvalBadge {{
                color: {accent}; background: {soft.name(QColor.HexArgb)};
                border-radius: 8px; padding: 3px 8px; font-weight: 600; font-size: 12px;
            }}
            #approvalTitle {{ font-size: 15px; font-weight: 600; }}
            #approvalHint, #approvalKeys, #fieldLabel {{ color: #8b879b; font-size: 12px; }}
            QPlainTextEdit {{
                background: #f4f2f8; border: 0; border-radius: 10px;
                padding: 8px; color: #2d2c38; font-family: {mono_font_css()}; font-size: 13px;
            }}
            QPushButton {{ border: 0; border-radius: 10px; padding: 8px 12px; font-family: {ui_font_css()}; font-size: 13px; }}
            #denyButton {{ background: #eeeaf6; color: #5c5678; }}
            #denyButton:hover {{ background: #e4def2; }}
            #allowButton {{ background: {accent}; color: white; font-weight: 700; }}
            #allowButton:hover {{ background: {accent}; }}
            #taskButton {{ background: #efeaf8; color: {accent}; font-weight: 700; }}
            #taskButton:hover {{ background: #e4def4; }}
        """)
        self.setFixedWidth(520)
        self._deny = deny

    def _add_body(self, layout, fields):
        mode = fields.get("mode")
        if mode == "edit":
            self._caption(layout, fields.get("path") or "将替换文件中的这一段")
            self._caption(layout, "原文")
            layout.addWidget(_block(fields.get("old", ""), 3))
            self._caption(layout, "改为")
            layout.addWidget(_block(fields.get("new", ""), 3))
            return
        if mode == "write":
            self._caption(layout, fields.get("path") or "文件")
            layout.addWidget(_block(fields.get("body", ""), 6))
            return
        if fields.get("command"):
            self._caption(layout, "将要运行")
            layout.addWidget(_block(fields["command"], 2))
        if fields.get("cwd"):
            self._caption(layout, "目录")
            layout.addWidget(_block(fields["cwd"], 1))
        if fields.get("path") and not fields.get("command"):
            self._caption(layout, "路径")
            layout.addWidget(_block(fields["path"], 1))
        if fields.get("reason"):
            self._caption(layout, "原因")
            layout.addWidget(_block(fields["reason"], 2))
        if fields.get("root"):
            self._caption(layout, "写入范围")
            layout.addWidget(_block(fields["root"], 1))
        if fields.get("permissions"):
            self._caption(layout, "权限")
            layout.addWidget(_block(fields["permissions"], 3))
        extra = fields.get("extra")
        if extra:
            layout.addWidget(_block(extra, 4))
        if mode != "fields" and not fields.get("command"):
            layout.addWidget(_block(fields.get("body", ""), 8))

    @staticmethod
    def _caption(layout, text):
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)

    def showEvent(self, event):
        super().showEvent(event)
        self.adjustSize()
        self._place()
        self._deny.setFocus()
        self.activateWindow()
        self.raise_()

    def _place(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        parent = self.parentWidget()
        anchor = parent.frameGeometry().center() if parent is not None and parent.isVisible() else area.center()
        x = max(area.left() + 12, min(anchor.x() - self.width() // 2, area.right() - self.width() - 12))
        y = max(area.top() + 12, min(anchor.y() - self.height() // 2, area.bottom() - self.height() - 12))
        self.move(x, y)

    def _choose(self, decision):
        self.decision = decision
        if decision == "deny":
            self.reject()
        else:
            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._choose("deny")
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self._choose("once")
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            return
        super().keyPressEvent(event)


def confirm_action(title, detail, parent=None, allow_task=True):
    """Return deny, once, or task. Closing the card counts as deny."""
    dialog = ApprovalDialog(title, detail, parent, allow_task=allow_task)
    dialog.exec_()
    return dialog.decision


def approval_summary(params):
    lines = []
    if params.get("command"):
        lines.append("命令：" + str(params["command"]))
    if params.get("cwd"):
        lines.append("目录：" + str(params["cwd"]))
    if params.get("reason"):
        lines.append("原因：" + str(params["reason"]))
    if params.get("grantRoot"):
        lines.append("写入范围：" + str(params["grantRoot"]))
    if params.get("permissions"):
        lines.append("权限：" + json.dumps(params["permissions"], ensure_ascii=False)[:2000])
    if not lines:
        lines.append(json.dumps(params, ensure_ascii=False)[:2000])
    return "\n".join(lines)
