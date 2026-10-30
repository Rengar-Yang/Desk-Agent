"""The perch at the top of the screen that holds the mascot."""

import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QCursor, QPainter, QPainterPath
from PyQt5.QtWidgets import QWidget

from orbit.config import get_config


class HomeNotch(QWidget):
    """Smooth inverted trapezoid attached to the top of the desktop."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.unit = get_config().mascot.scale
        self.setFixedSize(round(116 * self.unit), round(74 * self.unit))
        self.started = time.monotonic()
        self.closing = None
        self.hover = 0.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

    def reveal(self, screen):
        self.closing = None
        self.move(screen.center().x()-self.width()//2, screen.top())
        self.started = time.monotonic()
        self.show()

    def hold_open(self, screen):
        """Keep the envelope fully open while the mascot is still tied to it."""
        if self.closing is not None or not self.isVisible():
            self.reveal(screen)
        else:
            self.raise_()

    def dismiss(self):
        """Jelly-close the envelope after the mascot is pulled out of it."""
        if self.closing is not None or not self.isVisible():
            return
        self.closing = time.monotonic()
        self.raise_()

    def _close_pose(self, progress):
        keys = (
            (0.00, 1.00, 1.00),
            (0.14, 1.36, 1.10),
            (0.36, 0.06, 0.62),
            (0.52, 0.62, 1.08),
            (0.70, 0.00, 0.70),
            (0.84, 0.28, 0.94),
            (1.00, 0.00, 0.55),
        )
        for start, end in zip(keys, keys[1:]):
            if progress <= end[0]:
                span = end[0] - start[0]
                blend = 0 if span == 0 else (progress - start[0]) / span
                return start[1] + (end[1] - start[1]) * blend, start[2] + (end[2] - start[2]) * blend
        return 0., 0.55

    def paintEvent(self, event):
        now = time.monotonic()
        if self.closing is not None:
            elapsed = now - self.closing
            if elapsed >= 0.62:
                self.closing = None
                QTimer.singleShot(0, self.hide)
                return
            depth_scale, width_scale = self._close_pose(elapsed / 0.62)
            depth = 50 * depth_scale
        else:
            t = min(1., (now-self.started)/.62)
            spring = 1 + 2.4 * (t - 1) ** 3 + 1.4 * (t - 1) ** 2
            inside = self.geometry().contains(QCursor.pos())
            self.hover += ((1. if inside else 0.)-self.hover)*.18
            depth = 50 * spring + 5*self.hover
            width_scale = 1.
        if depth < 0.8:
            return
        center = 58
        half = 20 * width_scale
        left, right = center - half, center + half
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.unit, self.unit)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0e0e11"))
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(116, 0)
        path.cubicTo(right + 16, 0, right + 23, depth, right, depth)
        path.lineTo(left, depth)
        path.cubicTo(left - 23, depth, left - 16, 0, 0, 0)
        path.closeSubpath()
        p.drawPath(path)
        p.end()
