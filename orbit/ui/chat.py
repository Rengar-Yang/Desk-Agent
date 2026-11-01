"""The floating conversation card."""

import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QGuiApplication, QPixmap, QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizeGrip,
    QSizePolicy, QVBoxLayout, QWidget,
)

from orbit.config import get_config
from orbit.theme import ACCENTS, KIND_LABELS, context_headline, suggestion_prompts
from orbit.ui.fonts import ui_font, ui_font_css
from orbit.ui.markdown import markdown_to_html
from orbit.ui.mascot_icon import render_mascot

_FOLD_LIMIT = 420


class Composer(QPlainTextEdit):
    """Enter sends. Shift+Enter inserts a new line. IME composition is left alone."""

    submit_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._preedit = ""
        self.setPlaceholderText("让 Orbit 帮你做点什么…    Shift+Enter 换行")
        self.setTabChangesFocus(True)
        self.setFixedHeight(46)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def _fit(self):
        height = int(self.document().size().height()) + 18
        self.setFixedHeight(max(46, min(128, height)))

    def inputMethodEvent(self, event):
        self._preedit = event.preeditString() or ""
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            visible = QGuiApplication.inputMethod()
            composing = bool(self._preedit) or bool(visible and visible.isVisible())
            if composing:
                super().keyPressEvent(event)
                return
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class TextBubble(QLabel):
    def __init__(self, author, text):
        super().__init__()
        self.author = author
        self.raw_text = text
        self.folded = author == "工具" and len(text) > _FOLD_LIMIT
        self.setFont(ui_font(13))
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setOpenExternalLinks(author not in ("你", "错误", "工具"))
        self.setContentsMargins(11, 9, 11, 9)
        self.refresh()

    def mousePressEvent(self, event):
        if self.author == "工具" and len(self.raw_text) > _FOLD_LIMIT and event.button() == Qt.LeftButton:
            self.folded = not self.folded
            self.refresh()
            return
        super().mousePressEvent(event)

    def visible_text(self):
        if self.folded and len(self.raw_text) > _FOLD_LIMIT:
            return self.raw_text[:_FOLD_LIMIT].rstrip() + "\n… 点击展开"
        if self.author == "工具" and len(self.raw_text) > _FOLD_LIMIT:
            return self.raw_text + "\n… 点击收起"
        return self.raw_text

    def refresh(self):
        if self.author in ("你", "错误", "工具"):
            self.setTextFormat(Qt.PlainText)
            self.setText(self.visible_text())
            return
        self.setTextFormat(Qt.RichText)
        self.setText(markdown_to_html(self.visible_text()))


class ChatWindow(QWidget):
    send = pyqtSignal(str)
    stop = pyqtSignal()
    close_requested = pyqtSignal()
    reread = pyqtSignal()
    regenerate = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFont(ui_font(13))
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
        self.icon = QLabel()
        self.icon.setFixedSize(36, 36)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setPixmap(QPixmap.fromImage(render_mascot(36)))
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
        self.status.setMaximumHeight(64)
        self.status.setObjectName("status")
        root.addWidget(self.status)
        composer = QHBoxLayout()
        composer.setAlignment(Qt.AlignBottom)
        self.input = Composer()
        self.input.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.submit_requested.connect(self.submit)
        composer.addWidget(self.input, 1)
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(36, 36)
        self.send_button.clicked.connect(self.submit)
        composer.addWidget(self.send_button, 0, Qt.AlignBottom)
        root.addLayout(composer)
        footer = QHBoxLayout()
        self.footnote = QLabel(f"✦ {self.backend_label} · 拖到窗口后读取路径")
        self.footnote.setObjectName("footnote")
        self.footnote.setWordWrap(True)
        footer.addWidget(self.footnote, 1)
        self.retry_button = QPushButton("重说")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.clicked.connect(self.regenerate.emit)
        self.retry_button.hide()
        footer.addWidget(self.retry_button)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setObjectName("sizeGrip")
        footer.addWidget(self.size_grip)
        self.stop_button = QPushButton("结束任务")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.stop.emit)
        self.stop_button.hide()
        footer.addWidget(self.stop_button)
        root.addLayout(footer)
        self.streaming = False
        self.current_bubble = None
        self._suspend_changed = False
        self.apply_theme(ACCENTS["other"])
        self.set_context(None)

    def set_backend_label(self, label):
        self.backend_label = label or "本机 Codex"
        self.status.setText(f"{self.backend_label} · 点击发送以连接")
        if self.title.text() == "Orbit AI":
            self.footnote.setText(f"✦ {self.backend_label} · Enter 发送，Shift+Enter 换行")

    def assistant_name(self):
        return self.backend_label or "Orbit"

    def apply_theme(self, accent):
        self.accent = accent
        soft = QColor(accent)
        soft.setAlpha(28)
        banner = soft.name(QColor.HexArgb)
        self.setStyleSheet(f"""
            #chatCard {{ background: #fdfdff; border: 1px solid {accent}; border-radius: 19px; }}
            QLabel {{ color: #353448; background: transparent; font-family: {ui_font_css()}; font-size: 13px; }}
            #chatTitle {{ font-size: 15px; font-weight: 600; }}
            #chatPath, #footnote, #status {{ color: #8b879b; font-size: 12px; }}
            #contextBanner {{ background: {banner}; border-radius: 12px; }}
            #contextTitle {{ color: {accent}; font-weight: 600; }}
            #contextText {{ color: #8b82a7; font-size: 12px; }}
            QPlainTextEdit {{ border: 1px solid #e1ddec; border-radius: 11px; padding: 6px 10px; background: #fff; color: #343448; font-family: {ui_font_css()}; font-size: 13px; }}
            QPushButton {{ border: 0; border-radius: 10px; padding: 7px 10px; background: #eeeaf8; color: #655696; font-family: {ui_font_css()}; font-size: 13px; }}
            QPushButton:hover {{ background: #e4def5; }}
            #sendButton {{ background: {accent}; color: #fff; font-size: 17px; font-weight: 700; }}
            #closeButton {{ background: transparent; color: #89849b; font-size: 20px; }}
            #stopButton {{ background: #f3e4e8; color: #8d3d4a; font-weight: 600; }}
            #stopButton:hover {{ background: #ead4da; }}
            #retryButton, #copyButton, #rereadButton {{ background: transparent; color: #8b82a7; padding: 2px 6px; font-size: 12px; }}
            #retryButton:hover, #copyButton:hover, #rereadButton:hover {{ color: {accent}; background: transparent; }}
            #sizeGrip {{ background: transparent; width: 14px; height: 14px; }}
            #messageArea, #messageArea QWidget {{ border: 0; background: #fdfdff; }}
            #messageArea QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px 4px 0; }}
            #messageArea QScrollBar::handle:vertical {{ background: rgba(138, 128, 168, 110); min-height: 36px; border-radius: 4px; }}
            #messageArea QScrollBar::handle:vertical:hover {{ background: {accent}; }}
            #messageArea QScrollBar::add-line:vertical, #messageArea QScrollBar::sub-line:vertical {{ height: 0; width: 0; background: transparent; border: 0; }}
            #messageArea QScrollBar::add-page:vertical, #messageArea QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.icon.setPixmap(QPixmap.fromImage(render_mascot(36)))
        self.icon.setStyleSheet("background: transparent;")

    def set_context(self, target):
        while self.prompts.count():
            item = self.prompts.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if target:
            title = target["title"]
            kind = target.get("kind", "other")
            level = target.get("detail_level")
            self.apply_theme(target.get("accent") or ACCENTS.get(kind, ACCENTS["other"]))
            self.title.setText(f"已连接 · {title[:23]}")
            self.context.setText(target.get("path") or target.get("summary") or target.get("app") or "")
            if not level:
                self.context_title.setText("正在读取")
                self.context_text.setText("先确认这个窗口实际能提供的路径或正文。")
                prompts = []
            else:
                self.context_title.setText(context_headline(level))
                self.context_text.setText(self._context_body(target, level))
                prompts = suggestion_prompts(target)
            self.footnote.setText("✦ 写入和命令会先确认，可对本目录选择本次允许")
            reread = QPushButton("重新读取")
            reread.setObjectName("rereadButton")
            reread.clicked.connect(self.reread.emit)
            self.prompts.addWidget(reread)
        else:
            self.apply_theme(ACCENTS["other"])
            self.title.setText("Orbit AI")
            self.context.setText("待命中 · 可以直接聊天")
            self.context_title.setText("随时可以聊聊")
            self.context_text.setText("把我拖到窗口，或直接问我问题")
            self.footnote.setText(f"✦ {self.backend_label} · Enter 发送，Shift+Enter 换行")
            prompts = suggestion_prompts(None)
        for prompt in prompts:
            button = QPushButton(prompt)
            button.clicked.connect(lambda _=False, value=prompt: self.fill_prompt(value))
            self.prompts.addWidget(button)
        self._refresh_retry()

    @staticmethod
    def _context_body(target, level):
        summary = target.get("summary") or ""
        kind = KIND_LABELS.get(target.get("kind"), "窗口")
        if level == "title":
            return f"没有读到{kind}里的正文或路径。换个文件或标签后，点「重新读取」。"
        if level == "url":
            return summary or "只有地址，没有页面正文。需要正文时我会尝试抓取这个地址。"
        if level == "file_meta":
            return (summary or "定位到了文件") + "。正文没读到，可以点「重新读取」。"
        if level == "file":
            return summary or "已读取当前文件的开头。换文件后点「重新读取」。"
        return summary or f"{target.get('title', '')} · {target.get('app', '')}"

    def fill_prompt(self, value):
        if not self.send_button.isEnabled():
            return
        self.input.setPlainText(value)
        cursor = self.input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.input.setTextCursor(cursor)
        self.input.setFocus(Qt.ActiveWindowFocusReason)

    def clear_messages(self):
        self.reset_messages()
        self.append(self.assistant_name(), "对话已清空。")

    def reset_messages(self):
        self._suspend_changed = True
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.current_bubble = None
        self.streaming = False
        self._suspend_changed = False

    def restore_messages(self, items):
        self.reset_messages()
        if not items:
            return False
        self._suspend_changed = True
        for item in items:
            self.append(item.get("author") or "Orbit", item.get("text") or "")
        self._suspend_changed = False
        self._refresh_retry()
        return True

    def export_messages(self):
        items = []
        for index in range(self.message_layout.count()):
            row = self.message_layout.itemAt(index).widget()
            bubble = self._bubble_in(row)
            if bubble is not None and bubble.raw_text:
                items.append({"author": bubble.author, "text": bubble.raw_text})
        return items

    def recent_transcript(self, limit=8):
        items = [
            item for item in self.export_messages()
            if item["author"] not in ("工具", "错误")
        ]
        if items and items[-1]["author"] == "你":
            items = items[:-1]
        lines = []
        for item in items[-limit:]:
            lines.append(f"{item['author']}：{item['text'][:500]}")
        return "\n".join(lines)

    def has_user_message(self):
        return any(item["author"] == "你" for item in self.export_messages())

    def last_user_text(self):
        for item in reversed(self.export_messages()):
            if item["author"] == "你":
                return item["text"]
        return ""

    def drop_replies_after_last_user(self):
        last = -1
        rows = []
        for index in range(self.message_layout.count()):
            row = self.message_layout.itemAt(index).widget()
            bubble = self._bubble_in(row)
            rows.append(row)
            if bubble is not None and bubble.author == "你":
                last = index
        if last < 0:
            return
        for row in rows[last + 1:]:
            if row is None:
                continue
            self.message_layout.removeWidget(row)
            row.deleteLater()
        self.current_bubble = None
        self.streaming = False
        self._refresh_retry()
        self.changed.emit()

    def submit(self):
        value = self.input.toPlainText().strip()
        if value and self.send_button.isEnabled():
            self.input.clear()
            self.append("你", value)
            self.send_button.setEnabled(False)
            self.retry_button.hide()
            self.stop_button.show()
            self.send.emit(value)

    def append(self, author, text):
        bubble = TextBubble(author, text)
        bubble.setMaximumWidth(self.bubble_width())
        self._paint_bubble(bubble)
        copy = QPushButton("复制")
        copy.setObjectName("copyButton")
        copy.clicked.connect(lambda _=False, source=bubble: self.copy_text(source.raw_text))
        actions = QHBoxLayout()
        actions.setContentsMargins(4, 0, 4, 0)
        actions.setSpacing(4)
        if author == "你":
            actions.addStretch()
        actions.addWidget(copy)
        if author != "你":
            actions.addStretch()
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(bubble)
        column.addLayout(actions)
        holder = QWidget()
        holder.setLayout(column)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        if author == "你":
            layout.addStretch()
        layout.addWidget(holder)
        if author != "你":
            layout.addStretch()
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))
        if not self._suspend_changed:
            self.changed.emit()
        self._refresh_retry()
        return bubble

    def copy_text(self, text):
        QApplication.clipboard().setText(text or "")
        self.status.setText("已复制")

    @staticmethod
    def _bubble_in(row):
        if row is None or row.layout() is None:
            return None
        for index in range(row.layout().count()):
            widget = row.layout().itemAt(index).widget()
            if isinstance(widget, TextBubble):
                return widget
            if widget is not None and widget.layout() is not None:
                for child_index in range(widget.layout().count()):
                    child = widget.layout().itemAt(child_index).widget()
                    if isinstance(child, TextBubble):
                        return child
        return None

    @staticmethod
    def _paint_bubble(bubble):
        author = bubble.author
        if author == "你":
            bubble.setStyleSheet("background: #313040; color: white; border-radius: 12px; padding: 7px;")
        elif author == "错误":
            bubble.setStyleSheet("background: #fff0f1; color: #a94756; border-radius: 12px; padding: 7px;")
        elif author == "工具":
            bubble.setStyleSheet("background: #eef6f4; color: #2d6158; border-radius: 12px; padding: 7px;")
        else:
            bubble.setStyleSheet("background: #f0eff6; color: #414052; border-radius: 12px; padding: 7px;")

    def add_delta(self, text):
        if not self.streaming:
            self.current_bubble = self.append(self.assistant_name(), "")
            self.streaming = True
        self.current_bubble.raw_text += text
        self.current_bubble.refresh()
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum()))

    def set_busy_status(self, label, elapsed, stalled=False):
        dots = "·" * ((elapsed % 3) + 1)
        text = f"{label} {dots}  已进行 {elapsed} 秒"
        if stalled:
            text += "  ·  可能卡住了，可结束任务"
        self.status.setText(text)
        self.stop_button.setText("结束任务")
        self.stop_button.show()
        self.retry_button.hide()
        self.send_button.setEnabled(False)

    def finish(self, text):
        if not self.streaming:
            self.append(self.assistant_name(), text)
        else:
            self.changed.emit()
        self.streaming = False
        self.current_bubble = None
        self.send_button.setEnabled(True)
        self.stop_button.hide()
        self._refresh_retry()

    def _refresh_retry(self):
        self.retry_button.setVisible(self.send_button.isEnabled() and bool(self.last_user_text()))

    def bubble_width(self):
        return max(160, self.width() - 92)

    def is_user_resizing(self):
        return self._resizing is not None or time.monotonic() < self._resize_until

    def relayout_bubbles(self):
        if not hasattr(self, "message_layout"):
            return
        width = self.bubble_width()
        for index in range(self.message_layout.count()):
            bubble = self._bubble_in(self.message_layout.itemAt(index).widget())
            if bubble is not None and bubble.wordWrap():
                bubble.setMaximumWidth(width)

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
