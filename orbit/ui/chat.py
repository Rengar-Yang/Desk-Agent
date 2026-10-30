"""The floating conversation card."""

import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizeGrip, QSizePolicy, QVBoxLayout, QWidget,
)

from orbit.config import get_config
from orbit.theme import ACCENTS, CONTEXT_PROMPTS, KIND_LABELS
from orbit.ui.markdown import markdown_to_html


class ChatWindow(QWidget):
    send = pyqtSignal(str)
    stop = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setObjectName("chatCard")
        chat_size = get_config().chat
        self.backend_label = "本机 Codex"
        self.setMinimumSize(chat_size.min_width, chat_size.min_height)
        self.setMaximumSize(chat_size.max_width, chat_size.max_height)
        self.resize(chat_size.width, chat_size.height)
        self.setMouseTracking(True)
        self._resizing = None
        self._resize_until = 0.
        self._press_global = None
        self._press_geom = None
        self.accent = ACCENTS["other"]
        root = QVBoxLayout(self)
        root.setContentsMargins(17, 16, 17, 12)
        root.setSpacing(10)
        header = QHBoxLayout()
        self.icon = QLabel("✦")
        self.icon.setFixedSize(36, 36)
        self.icon.setAlignment(Qt.AlignCenter)
        header.addWidget(self.icon)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        self.title = QLabel("Orbit AI")
        self.title.setObjectName("chatTitle")
        self.context = QLabel("待命中 · 可以直接聊天")
        self.context.setObjectName("chatPath")
        self.context.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.context.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        heading.addWidget(self.title)
        heading.addWidget(self.context)
        header.addLayout(heading, 1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setFixedSize(29, 29)
        close.clicked.connect(self.close_requested.emit)
        header.addWidget(close)
        root.addLayout(header)
        banner = QWidget()
        banner.setObjectName("contextBanner")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(13, 10, 13, 10)
        banner_layout.setSpacing(2)
        self.context_title = QLabel("随时可以聊聊")
        self.context_title.setObjectName("contextTitle")
        self.context_text = QLabel("把我拖到窗口，或直接问我问题")
        self.context_text.setObjectName("contextText")
        self.context_text.setWordWrap(True)
        banner_layout.addWidget(self.context_title)
        banner_layout.addWidget(self.context_text)
        root.addWidget(banner)
        self.area = QScrollArea()
        self.area.setObjectName("messageArea")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area.verticalScrollBar().setSingleStep(28)
        self.messages = QWidget()
        self.message_layout = QVBoxLayout(self.messages)
        self.message_layout.setContentsMargins(2, 3, 2, 3)
        self.message_layout.setSpacing(9)
        self.message_layout.addStretch()
        self.area.setWidget(self.messages)
        root.addWidget(self.area, 1)
        self.prompts = QHBoxLayout()
        self.prompts.setSpacing(5)
        root.addLayout(self.prompts)
        self.status = QLabel(f"{self.backend_label} · 点击发送以连接")
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(40)
        self.status.setObjectName("status")
        root.addWidget(self.status)
        composer = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setPlaceholderText("让 Orbit 帮你做点什么...")
        self.input.returnPressed.connect(self.submit)
        composer.addWidget(self.input, 1)
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(36, 36)
        self.send_button.clicked.connect(self.submit)
        composer.addWidget(self.send_button)
        root.addLayout(composer)
        footer = QHBoxLayout()
        self.footnote = QLabel(f"✦ {self.backend_label} · 拖到窗口后读取路径")
        self.footnote.setObjectName("footnote")
        footer.addWidget(self.footnote)
        footer.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setObjectName("sizeGrip")
        footer.addWidget(self.size_grip)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop.emit)
        self.stop_button.hide()
        footer.addWidget(self.stop_button)
        root.addLayout(footer)
        self.streaming = False
        self.current_bubble = None
        self.apply_theme(ACCENTS["other"])
        self.set_context(None)
        self.append("Codex", "你好！点击我可以随时聊天，也可以把我拖到窗口上一起工作。")

    def set_backend_label(self, label):
        self.backend_label = label or "本机 Codex"
        self.status.setText(f"{self.backend_label} · 点击发送以连接")
        if self.title.text() == "Orbit AI":
            self.footnote.setText(f"✦ {self.backend_label} · 拖到窗口后读取路径")

    def apply_theme(self, accent):
        self.accent = accent
        soft = QColor(accent)
        soft.setAlpha(28)
        banner = soft.name(QColor.HexArgb)
        self.setStyleSheet(f"""
            #chatCard {{ background: #fdfdff; border: 1px solid {accent}; border-radius: 19px; }}
            QLabel {{ color: #353448; background: transparent; font: 12px 'Noto Sans CJK SC'; }}
            #chatTitle {{ font-size: 14px; font-weight: 700; }}
            #chatPath, #footnote, #status {{ color: #8b879b; font-size: 10px; }}
            #contextBanner {{ background: {banner}; border-radius: 12px; }}
            #contextTitle {{ color: {accent}; font-weight: 700; }}
            #contextText {{ color: #8b82a7; font-size: 10px; }}
            QLineEdit {{ border: 1px solid #e1ddec; border-radius: 11px; padding: 8px 12px; background: #fff; color: #343448; }}
            QPushButton {{ border: 0; border-radius: 10px; padding: 7px 10px; background: #eeeaf8; color: #655696; font-size: 11px; }}
            QPushButton:hover {{ background: #e4def5; }}
            #sendButton {{ background: {accent}; color: #fff; font-size: 17px; font-weight: 700; }}
            #closeButton {{ background: transparent; color: #89849b; font-size: 20px; }}
            #sizeGrip {{ background: transparent; width: 14px; height: 14px; }}
            #messageArea, #messageArea QWidget {{ border: 0; background: #fdfdff; }}
            #messageArea QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px 4px 0; }}
            #messageArea QScrollBar::handle:vertical {{ background: rgba(138, 128, 168, 110); min-height: 36px; border-radius: 4px; }}
            #messageArea QScrollBar::handle:vertical:hover {{ background: {accent}; }}
            #messageArea QScrollBar::add-line:vertical, #messageArea QScrollBar::sub-line:vertical {{ height: 0; width: 0; background: transparent; border: 0; }}
            #messageArea QScrollBar::add-page:vertical, #messageArea QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.icon.setStyleSheet(f"background: {banner}; color: {accent}; border-radius: 10px; font-size: 18px;")

    def set_context(self, target):
        while self.prompts.count():
            item = self.prompts.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if target:
            title = target["title"]
            kind = target.get("kind", "other")
            self.apply_theme(target.get("accent") or ACCENTS.get(kind, ACCENTS["other"]))
            self.title.setText(f"已连接 · {title[:23]}")
            self.context.setText(target.get("path") or target.get("summary") or target["app"])
            self.context_title.setText(f"已读取{KIND_LABELS.get(kind, '窗口')}上下文")
            self.context_text.setText(target.get("summary") or f"{title} · {target['app']}")
            self.footnote.setText("✦ 可读取和修改该目录，写入与命令会先确认")
            prompts = CONTEXT_PROMPTS[kind]
        else:
            self.apply_theme(ACCENTS["other"])
            self.title.setText("Orbit AI")
            self.context.setText("待命中 · 可以直接聊天")
            self.context_title.setText("随时可以聊聊")
            self.context_text.setText("把我拖到窗口，或直接问我问题")
            self.footnote.setText(f"✦ {self.backend_label} · 拖到窗口后读取路径")
            prompts = CONTEXT_PROMPTS[None]
        for prompt in prompts:
            button = QPushButton(prompt)
            button.clicked.connect(lambda _=False, value=prompt: self.send_prompt(value))
            self.prompts.addWidget(button)

    def send_prompt(self, value):
        if self.send_button.isEnabled():
            self.input.setText(value)
            self.submit()

    def clear_messages(self):
        self.reset_messages()
        self.append("Codex", "对话已清空。")

    def reset_messages(self):
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.current_bubble = None
        self.streaming = False

    def submit(self):
        value = self.input.text().strip()
        if value and self.send_button.isEnabled():
            self.input.clear()
            self.append("你", value)
            self.send_button.setEnabled(False)
            self.stop_button.show()
            self.send.emit(value)

    def append(self, author, text):
        bubble = QLabel()
        bubble.raw_text = text
        bubble.author = author
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setOpenExternalLinks(author not in ("你", "错误", "工具"))
        bubble.setMaximumWidth(self.bubble_width())
        bubble.setContentsMargins(11, 9, 11, 9)
        if author == "你":
            bubble.setStyleSheet("background: #313040; color: white; border-radius: 12px; padding: 7px;")
        elif author == "错误":
            bubble.setStyleSheet("background: #fff0f1; color: #a94756; border-radius: 12px; padding: 7px;")
        elif author == "工具":
            bubble.setStyleSheet("background: #eef6f4; color: #2d6158; border-radius: 12px; padding: 7px;")
        else:
            bubble.setStyleSheet("background: #f0eff6; color: #414052; border-radius: 12px; padding: 7px;")
        self.render_bubble(bubble)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        if author == "你": layout.addStretch()
        layout.addWidget(bubble)
        if author != "你": layout.addStretch()
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))
        return bubble

    def render_bubble(self, bubble):
        if bubble.author in ("你", "错误", "工具"):
            bubble.setTextFormat(Qt.PlainText)
            bubble.setText(bubble.raw_text)
            return
        bubble.setTextFormat(Qt.RichText)
        bubble.setText(markdown_to_html(bubble.raw_text))

    def add_delta(self, text):
        if not self.streaming:
            self.current_bubble = self.append("Codex", "")
            self.streaming = True
        self.current_bubble.raw_text += text
        self.render_bubble(self.current_bubble)
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))

    def finish(self, text):
        if not self.streaming: self.append("Codex", text)
        self.streaming = False
        self.current_bubble = None
        self.send_button.setEnabled(True)
        self.stop_button.hide()

    def bubble_width(self):
        return max(160, self.width() - 92)

    def is_user_resizing(self):
        return self._resizing is not None or time.monotonic() < self._resize_until

    def relayout_bubbles(self):
        if not hasattr(self, "message_layout"):
            return
        width = self.bubble_width()
        for index in range(self.message_layout.count()):
            row = self.message_layout.itemAt(index).widget()
            layout = row.layout() if row else None
            if layout is None:
                continue
            for child_index in range(layout.count()):
                widget = layout.itemAt(child_index).widget()
                if isinstance(widget, QLabel) and widget.wordWrap():
                    widget.setMaximumWidth(width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_until = time.monotonic() + 0.2
        self.relayout_bubbles()

    def _hit_edge(self, pos):
        margin = 7
        left = pos.x() <= margin
        right = pos.x() >= self.width() - margin
        top = pos.y() <= margin
        bottom = pos.y() >= self.height() - margin
        if not (left or right or top or bottom):
            return None
        return (left, right, top, bottom)

    def _cursor_for(self, edge):
        if edge is None:
            return Qt.ArrowCursor
        left, right, top, bottom = edge
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    def mousePressEvent(self, event):
        edge = self._hit_edge(event.pos()) if event.button() == Qt.LeftButton else None
        if edge:
            self._resizing = edge
            self._press_global = event.globalPos()
            self._press_geom = self.geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and event.buttons() & Qt.LeftButton:
            self._apply_resize(event.globalPos())
            event.accept()
            return
        self.setCursor(self._cursor_for(self._hit_edge(event.pos())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = None
            self._resize_until = time.monotonic() + 0.2
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_resize(self, global_pos):
        delta = global_pos - self._press_global
        geom = self._press_geom
        left, right, top, bottom = self._resizing
        x, y, width, height = geom.x(), geom.y(), geom.width(), geom.height()
        if left:
            width -= delta.x()
        if right:
            width += delta.x()
        if top:
            height -= delta.y()
        if bottom:
            height += delta.y()
        width = max(self.minimumWidth(), min(self.maximumWidth(), width))
        height = max(self.minimumHeight(), min(self.maximumHeight(), height))
        if left:
            x = geom.right() - width + 1
        if top:
            y = geom.bottom() - height + 1
        self.setGeometry(x, y, width, height)
