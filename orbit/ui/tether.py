"""The elastic band drawn between the drag origin and the mascot."""

import math
import time

from PyQt5.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import QWidget

from orbit.config import get_config


class TetherWindow(QWidget):
    """Separate click-through surface so the black elastic band spans the desktop."""

    snapped = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.start = None
        self.end = None
        self.broken = False
        self.release_started = None
        self._snap_pending = False
        self.release_duration = .28
        self.unit = get_config().mascot.scale
        self.break_distance = get_config().tether.break_distance
        self.bend = 0.0
        self._last_end = None
        self.flow = QTimer(self)
        self.flow.timeout.connect(self.tick)
        self.flow.start(16)

    def tick(self):
        if self.release_started and time.monotonic() - self.release_started >= self.release_duration:
            self.release_started = None
            self.hide()
            if self._snap_pending:
                self._snap_pending = False
                self.snapped.emit()
        elif self.isVisible():
            if abs(self.bend) > 0.04:
                self.bend *= 0.96
            self.update()

    def release(self, duration=.28):
        if self.isVisible() and self.release_started is None:
            self.release_duration = duration
            self.release_started = time.monotonic()
            self.broken = True
            self._snap_pending = True

    def reset_motion(self):
        """Forget the previous drag so the next pull starts as a round blob."""
        self.bend = 0.0
        self._last_end = None

    @staticmethod
    def ribbon(a, b, ra, rb, waist, bend=0.0):
        """A variable-width band whose ends are semicircles, never flat cuts.

        A straight cap (lineTo across the end) meets the sides at a corner.
        Sampling the outline and turning each end through a half-circle keeps
        the whole pull, including the first few pixels, rounded.
        """
        sx, sy = float(a.x()), float(a.y())
        ex, ey = float(b.x()), float(b.y())
        dx, dy = ex - sx, ey - sy
        length = math.hypot(dx, dy)
        ra = max(2.0, float(ra))
        rb = max(2.0, float(rb))
        waist = max(2.0, float(waist))
        if length < 1.5:
            radius = max(ra, rb, waist)
            path = QPainterPath()
            path.addEllipse(sx - radius, sy - radius, radius * 2, radius * 2)
            return path
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        bend = float(bend) * min(1.0, length / 90.0)
        mid_x = (sx + ex) / 2 + nx * bend
        mid_y = (sy + ey) / 2 + ny * bend
        # While the pull is still short, keep the middle as wide as the ends
        # so the shape is a capsule instead of a pinched rectangle.
        pinch = min(1.0, max(0.0, (length - 36.0) / 120.0))
        waist = waist * pinch + max(ra, rb) * (1.0 - pinch)

        def point_at(t):
            omt = 1.0 - t
            x = omt * omt * sx + 2 * omt * t * mid_x + t * t * ex
            y = omt * omt * sy + 2 * omt * t * mid_y + t * t * ey
            edge = ra * omt + rb * t
            belly = math.sin(math.pi * t)
            return x, y, max(2.0, edge * (1.0 - belly) + waist * belly)

        samples = 24
        centers = [point_at(i / samples) for i in range(samples + 1)]

        def tangent_at(index):
            if index == 0:
                tx = centers[1][0] - centers[0][0]
                ty = centers[1][1] - centers[0][1]
            elif index == samples:
                tx = centers[index][0] - centers[index - 1][0]
                ty = centers[index][1] - centers[index - 1][1]
            else:
                tx = centers[index + 1][0] - centers[index - 1][0]
                ty = centers[index + 1][1] - centers[index - 1][1]
            mag = math.hypot(tx, ty) or 1.0
            return tx / mag, ty / mag

        def arc_points(cx, cy, radius, nx0, ny0, outward_x, outward_y):
            start = math.atan2(ny0, nx0)
            midpoint = start + math.pi / 2
            target = math.atan2(outward_y, outward_x)
            delta = math.atan2(math.sin(midpoint - target), math.cos(midpoint - target))
            sweep = math.pi if abs(delta) <= math.pi / 2 else -math.pi
            points = []
            steps = 14
            for step in range(1, steps):
                angle = start + sweep * step / steps
                points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
            return points

        right = []
        left = []
        for index, (x, y, radius) in enumerate(centers):
            tx, ty = tangent_at(index)
            right.append((x - ty * radius, y + tx * radius))
            left.append((x + ty * radius, y - tx * radius))
        end_tx, end_ty = tangent_at(samples)
        end_x, end_y, end_r = centers[-1]
        start_tx, start_ty = tangent_at(0)
        start_x, start_y, start_r = centers[0]
        path = QPainterPath()
        path.moveTo(*right[0])
        for point in right[1:]:
            path.lineTo(*point)
        for point in arc_points(end_x, end_y, end_r, -end_ty, end_tx, end_tx, end_ty):
            path.lineTo(*point)
        for point in reversed(left):
            path.lineTo(*point)
        for point in arc_points(start_x, start_y, start_r, start_ty, -start_tx, -start_tx, -start_ty):
            path.lineTo(*point)
        path.closeSubpath()
        return path

    def update_points(self, start, end):
        distance = math.hypot(start.x() - end.x(), start.y() - end.y())
        if self.broken:
            return
        if self._last_end is not None and distance > 1:
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            side = (-dy * (end.x() - self._last_end.x()) + dx * (end.y() - self._last_end.y())) / distance
            pulled = max(-22.0, min(22.0, side * 1.2))
            self.bend = self.bend * 0.55 + pulled * 0.45
        self._last_end = QPointF(end)
        self.start = start
        self.end = end
        margin = int(math.ceil(40 * self.unit + abs(self.bend)))
        left = min(start.x(), end.x()) - margin
        top = min(start.y(), end.y()) - margin
        self.setGeometry(int(left), int(top), int(abs(start.x() - end.x()) + margin * 2), int(abs(start.y() - end.y()) + margin * 2))
        self.show()
        self.update()
        if distance > self.break_distance:
            self.release()

    def paintEvent(self, _event):
        if not self.start or not self.end:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        a = self.start - self.pos()
        b = self.end - self.pos()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = max(1.0, math.hypot(dx, dy))
        u = self.unit
        span = max(1.0, self.break_distance - 12)
        tension = max(0.0, min(1.0, (length - 12) / span))
        ripple = math.sin(time.monotonic() * 14) * 1.15 * u * math.sin(math.pi * tension)
        waist = max(2.2 * u, 14 * u - 11.5 * u * tension ** 0.58 + ripple)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2d2d31"))
        if self.release_started:
            progress = min(1, (time.monotonic() - self.release_started) / self.release_duration)
            eased = 1 - (1 - progress) ** 3
            reach = 0.5 - 0.47 * eased
            painter.setOpacity(min(1, (1 - progress) * 1.5))
            tip_a = QPointF(a.x() + dx * reach, a.y() + dy * reach)
            tip_b = QPointF(b.x() - dx * reach, b.y() - dy * reach)
            painter.drawPath(self.ribbon(a, tip_a, 13 * u * (1 - eased * 0.5), max(0.8, 8 * u * (1 - eased)), max(0.8, waist * (1 - eased)), 0))
            painter.drawPath(self.ribbon(b, tip_b, 16 * u * (1 - eased * 0.5), max(0.8, 8 * u * (1 - eased)), max(0.8, waist * (1 - eased)), 0))
            nx, ny = -dy / length, dx / length
            mid_x, mid_y = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
            for i in range(3):
                spread = (i - 1) * (9 + 22 * eased) * u
                painter.drawEllipse(QPointF(mid_x + nx * spread, mid_y + ny * spread), float((4 - i) * u), float((4 - i) * u))
        else:
            painter.drawPath(self.ribbon(
                a, b, (16 - tension * 5) * u, (18 - tension * 4) * u, waist, self.bend,
            ))
        painter.end()
