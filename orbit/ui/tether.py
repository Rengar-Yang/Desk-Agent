"""The elastic band drawn between the home perch and the mascot."""

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
        elif self.isVisible(): self.update()

    def release(self, duration=.28):
        if self.isVisible() and self.release_started is None:
            self.release_duration = duration
            self.release_started = time.monotonic()
            self.broken = True
            self._snap_pending = True

    @staticmethod
    def ribbon(a, b, ra, rb, waist):
        dx, dy = b.x()-a.x(), b.y()-a.y()
        length = max(1, math.hypot(dx,dy))
        ux, uy = dx/length, dy/length
        nx, ny = -uy, ux
        pull = min(length*.22,40)
        points = lambda x,y,r: (x+nx*r,y+ny*r)
        mid = ((a.x()+b.x())/2,(a.y()+b.y())/2)
        p = QPainterPath()
        s,e,m = points(a.x(),a.y(),ra),points(b.x(),b.y(),rb),points(*mid,waist)
        p.moveTo(*s)
        p.cubicTo(s[0]+ux*pull,s[1]+uy*pull,m[0]-ux*pull,m[1]-uy*pull,*m)
        p.cubicTo(m[0]+ux*pull,m[1]+uy*pull,e[0]-ux*pull,e[1]-uy*pull,*e)
        s,e,m = points(a.x(),a.y(),-ra),points(b.x(),b.y(),-rb),points(*mid,-waist)
        p.lineTo(*e)
        p.cubicTo(e[0]-ux*pull,e[1]-uy*pull,m[0]+ux*pull,m[1]+uy*pull,*m)
        p.cubicTo(m[0]-ux*pull,m[1]-uy*pull,s[0]+ux*pull,s[1]+uy*pull,*s)
        p.closeSubpath()
        return p

    def update_points(self, start, end):
        distance = math.hypot(start.x() - end.x(), start.y() - end.y())
        if self.broken:
            return
        self.start = start
        self.end = end
        left = min(start.x(), end.x()) - 24
        top = min(start.y(), end.y()) - 24
        self.setGeometry(left, top, abs(start.x() - end.x()) + 48, abs(start.y() - end.y()) + 48)
        self.show()
        self.update()
        if distance > self.break_distance: self.release()

    def paintEvent(self, _event):
        if not self.start or not self.end:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        a = self.start - self.pos()
        b = self.end - self.pos()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = max(1, (dx * dx + dy * dy) ** 0.5)
        nx, ny = -dy / length, dx / length
        u = self.unit
        waist = max(2.5 * u, 14 * u - length / 21)
        width_a, width_b = 14 * u, 15 * u
        mid_x, mid_y = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
        path = QPainterPath()
        path.moveTo(a.x() + nx * width_a, a.y() + ny * width_a)
        path.quadTo(mid_x + nx * waist, mid_y + ny * waist, b.x() + nx * width_b, b.y() + ny * width_b)
        path.lineTo(b.x() - nx * width_b, b.y() - ny * width_b)
        path.quadTo(mid_x - nx * waist, mid_y - ny * waist, a.x() - nx * width_a, a.y() - ny * width_a)
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2d2d31"))
        span = max(1, self.break_distance - 12)
        tension = max(0, min(1, (length - 12) / span))
        u = self.unit
        waist = max(2.2 * u, 14 * u - 11.5 * u * tension ** .58 + math.sin(time.monotonic() * 14) * 1.15 * u * math.sin(math.pi * tension))
        if self.release_started:
            progress = min(1,(time.monotonic()-self.release_started)/self.release_duration)
            eased = 1-(1-progress)**3
            reach = .5-.47*eased
            painter.setOpacity(min(1,(1-progress)*1.5))
            tip_a = QPointF(a.x()+dx*reach,a.y()+dy*reach)
            tip_b = QPointF(b.x()-dx*reach,b.y()-dy*reach)
            painter.drawPath(self.ribbon(a,tip_a,13*u*(1-eased*.5),max(.3,8*u*(1-eased)),max(.3,waist*(1-eased))))
            painter.drawPath(self.ribbon(b,tip_b,16*u*(1-eased*.5),max(.3,8*u*(1-eased)),max(.3,waist*(1-eased))))
            for i in range(3):
                spread=(i-1)*(9+22*eased)*u
                painter.drawEllipse(QPointF(mid_x+nx*spread,mid_y+ny*spread),float((4-i)*u),float((4-i)*u))
        else:
            painter.drawPath(self.ribbon(a,b,(16-tension*5)*u,(18-tension*4)*u,waist))
        painter.end()
