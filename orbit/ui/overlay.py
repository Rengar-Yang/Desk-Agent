"""Highlight the window under the mascot while it is dragged."""

import math
import time

from PyQt5.QtCore import QRect, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from orbit.theme import ACCENTS


class TargetOverlay(QWidget):
    """A click-through target outline and hint while the mascot is dragged."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.target = None
        self.show_hint = True
        self.connected = False
        self.accent = "#8a72d5"
        self.connected_at = None
        self.pulse = QTimer(self)
        self.pulse.timeout.connect(self.update)
        self.pulse.start(16)

    def set_target(self, target, show_hint=True, connected=False):
        if not target:
            self.target = None
            self.connected = False
            self.connected_at = None
            self.hide()
            return
        switched = not self.target or self.target.get("id") != target.get("id") or self.connected != connected
        self.target = target
        self.show_hint = show_hint
        if connected and not self.connected:
            self.connected_at = time.monotonic()
        elif not connected:
            self.connected_at = None
        self.connected = connected
        self.accent = target.get("accent") or ACCENTS.get(target.get("kind"), "#8a72d5")
        rect = target["rect"]
        pad = 26
        self.setGeometry(rect.adjusted(-pad, -pad, pad, pad))
        if switched:
            self.show()
        self.update()

    def paintEvent(self, _event):
        if not self.target:
            return
        pad = 26
        inner = QRect(pad, pad, self.width() - pad * 2, self.height() - pad * 2)
        phase = (1 - math.cos(time.monotonic() * 2 * math.pi / 1.2)) / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(self.accent)
        if not self.connected:
            glow = QColor(accent)
            glow.setAlpha(int(16 + 30 * phase))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            spread = int(8 + 12 * phase)
            p.drawRoundedRect(inner.adjusted(-spread, -spread, spread, spread), 16, 16)
            p.setPen(QPen(accent, int(3 + phase)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(inner.adjusted(2, 2, -3, -3), 8, 8)
        else:
            p.setPen(QPen(accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(inner.adjusted(1, 1, -2, -2), 8, 8)
            if self.connected_at:
                sweep = (time.monotonic() - self.connected_at) / .85
                if 0 <= sweep <= 1:
                    band = QColor(accent)
                    band.setAlpha(int(70 * (1 - sweep)))
                    p.setPen(Qt.NoPen)
                    p.setBrush(band)
                    p.drawRoundedRect(int(inner.left() + (inner.width() + 40) * sweep - 50), inner.top(), 42, 28, 8, 8)
        if self.show_hint or self.connected:
            text = ("✦  ORBIT 已连接" if self.connected else f"✦  松开以连接 · {self.target['title'][:30]}")
            metrics = p.fontMetrics()
            width = min(inner.width() - 20, metrics.horizontalAdvance(text) + 28)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#fbfaff"))
            p.drawRoundedRect(inner.left() + 12, inner.top() + 12, width, 32, 10, 10)
            p.setPen(accent)
            p.drawText(QRect(inner.left() + 24, inner.top() + 12, width - 16, 32), Qt.AlignVCenter, text)
        p.end()
