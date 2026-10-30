"""The desktop mascot: dragging, perching, and sending a message."""

import json
import math
import os
import random
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from PyQt5.QtCore import (
    QAbstractAnimation, QObject, QPoint, QRect, Qt, QThread, QTimer, QEasingCurve, QPropertyAnimation, pyqtSignal,
)
from PyQt5.QtGui import QColor, QCursor, QFont, QPainter, QPen, QRegion
from PyQt5.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QWidget

from orbit.agent.codex import CodexBridge
from orbit.agent.tools import API_TOOLS, api_error_text, kill_running_shell, run_tool, tool_label
from orbit.config import get_config, load_api_token, load_user_settings, save_user_settings
from orbit.desktop.context import capture_context, context_prompt
from orbit.desktop.common import hydrate_window, topmost_at
from orbit.theme import ACCENTS, KIND_LABELS
from orbit.ui.chat import ChatWindow
from orbit.ui.confirm import confirm_action
from orbit.ui.notch import HomeNotch
from orbit.ui.overlay import TargetOverlay
from orbit.ui.probe import WindowProbe
from orbit.desktop.autostart import set_login_autostart
from orbit.ui.settings import SettingsDialog
from orbit.ui.tether import TetherWindow


class ContextBridge(QObject):
    ready = pyqtSignal(int, str, object)


class Pet(QWidget):
    api_result = pyqtSignal(int, object)
    tool_log = pyqtSignal(str, str)
    tools_ready = pyqtSignal(int, object, object)
    confirm_request = pyqtSignal()
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        # Keep a generous transparent canvas around the smaller painted mascot.
        mascot = get_config().mascot
        self.unit = mascot.scale
        self.setFixedSize(mascot.canvas_width, mascot.canvas_height)
        self.pet_scale = mascot.paint_scale
        self.pet_padding = 24 * self.unit
        self.docked = True
        self.drag_growth = 1.
        self.drag_from_home = False
        self.next_idle = time.monotonic() + random.uniform(4, 9)
        self.idle_until = 0.
        self.idle_kind = "blink"
        self.gaze_x = self.gaze_y = 0.
        self.notch = HomeNotch()
        self.settings = load_user_settings()
        self.api_token = load_api_token()
        self.api_generation = 0
        self.api_round = 0
        self.api_messages = []
        self.api_inflight = False
        self.api_tool_running = False
        self.api_history = []
        self.api_result.connect(self.api_finished)
        self.tool_log.connect(self.on_tool_log)
        self.tools_ready.connect(self.on_tools_ready)
        self.confirm_request.connect(self._confirm_on_gui, Qt.BlockingQueuedConnection)
        self._confirm_box = None
        self.context_pending = False
        self.pending_message = None
        self.bridge = CodexBridge()
        self.chat = ChatWindow()
        self.chat.set_backend_label(self.backend_label())
        self.chat.send.connect(self.ask)
        self.chat.stop.connect(self.stop_reply)
        self.chat.close_requested.connect(self.close_chat)
        self.bridge.status.connect(self.on_status)
        self.bridge.delta.connect(self.chat.add_delta)
        self.bridge.finished.connect(self.on_finished)
        self.bridge.error.connect(self.on_error)
        self.tether = TetherWindow()
        self.tether.snapped.connect(self._on_tether_snapped)
        self._close_notch_after_snap = False
        self._notch_released = False
        self.overlay = TargetOverlay()
        self.drag_start = None
        self.dragged = False
        self.target = None
        self.hover_target = None
        self.cached_windows = []
        self.held_id = None
        self.context_token = 0
        self.misses = 0
        self.dock_started = None
        self._mask_key = None
        self.tether_from = None
        self.mood = "idle"
        self.mood_started = time.monotonic()
        self.landing_started = None
        self.phase = 0
        self.bounce = 0
        self.live_rect = None
        self.buried = False
        self.chat_was_open = False
        self.grip = 0.
        self.slip = 0.
        self.slip_v = 0.
        self.lean = 0.
        self.lean_v = 0.
        self.ride_vx = 0.
        self.ride_vy = 0.
        self.ride_at = 0.
        self._frame_at = time.monotonic()
        self.animation = QPropertyAnimation(self, b"pos", self)
        self.animation.setEasingCurve(QEasingCurve.OutBack)
        self.animation.setDuration(500)
        self.animation.finished.connect(self.finish_return)
        curve = QEasingCurve(QEasingCurve.OutBack)
        curve.setOvershoot(1.35)
        self.chat_motion = QPropertyAnimation(self.chat, b"pos", self)
        self.chat_motion.setEasingCurve(curve)
        self.chat_fade = QPropertyAnimation(self.chat, b"windowOpacity", self)
        self.chat_fade.setEasingCurve(QEasingCurve.OutCubic)
        self.probe = WindowProbe()
        self.probe.found.connect(self.on_windows)
        self.probe.placed.connect(self.on_window_moved)
        self.context_bridge = ContextBridge()
        self.context_bridge.ready.connect(self.apply_context)
        behavior = get_config().behavior
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.follow_target)
        self.timer.start(behavior.follow_ms)
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.request_windows)
        self.scan_timer.start(behavior.scan_ms)
        self.motion = QTimer(self)
        self.motion.timeout.connect(self.animate_pet)
        self.motion.start(16)
        screen = QApplication.primaryScreen().availableGeometry()
        self.home = QPoint(screen.center().x() - self.width() // 2, screen.top())
        self.move(self.home)
        self.notch.reveal(screen)
        self.show()
        self.refresh_mask()
        QTimer.singleShot(200, self.request_windows)

    def backend_label(self):
        return "自定义 API" if self.settings.get("provider") == "api" else "本机 Codex"

    def load_settings(self):
        return load_user_settings()

    def animate_pet(self):
        now = time.monotonic()
        if self.mood == "idle" and not self.drag_start and now >= self.next_idle:
            self.idle_kind = random.choice(["blink", "hop"])
            self.idle_until = now + .9
            self.next_idle = now + random.uniform(5, 12)
        center = self.visual_center()
        delta = QCursor.pos()-center
        distance = math.hypot(delta.x(), delta.y())
        if distance > 1:
            gx = max(-8.5, min(8.5, delta.x() / 18))
            gy = max(-5.5, min(5.5, delta.y() / 22))
        else:
            gx = gy = 0.
        self.gaze_x += (gx-self.gaze_x)*.28
        self.gaze_y += (gy-self.gaze_y)*.28
        self.phase += 0.16
        if self.bounce > 0:
            self.bounce = max(0, self.bounce - 1)
        frame = min(0.05, max(0.001, now - self._frame_at))
        self._frame_at = now
        if now - self.ride_at > 0.09:
            self.ride_vx *= 0.84
            self.ride_vy *= 0.84
        speed = math.hypot(self.ride_vx, self.ride_vy)
        grip_target = min(1., max(0., (speed - 36) / 620))
        self.grip += (grip_target - self.grip) * min(1., frame * 10)
        if grip_target > 0.1:
            lean_target = max(-7., min(7., -self.ride_vx / 140))
            slip_target = max(-10., min(6., -self.ride_vy / 170))
            self.lean += (lean_target - self.lean) * min(1., frame * 14)
            self.slip += (slip_target - self.slip) * min(1., frame * 14)
            self.lean_v = self.slip_v = 0.
        else:
            self.slip_v += (-36 * self.slip - 9 * self.slip_v) * frame
            self.slip += self.slip_v * frame
            self.lean_v += (-38 * self.lean - 9 * self.lean_v) * frame
            self.lean += self.lean_v * frame
        self.refresh_mask()
        self.update()

    def placement(self):
        """Return the mascot's unscaled origin and scale inside the widget."""
        u = self.unit
        scale_free = self.pet_scale * (self.drag_growth if self.drag_from_home and self.dragged else 1)
        cy = (24 + 68 * (self.drag_growth - .5)) * u if self.drag_from_home and self.dragged else 58 * u
        free = (56 * u - 41 * scale_free, cy - 43 * scale_free, scale_free)
        scale_dock = self.pet_scale * .5
        dock = (self.width() / 2 - 41 * scale_dock, 24 * u - 36 * scale_dock, scale_dock)
        if self.dock_started:
            progress = min(1, (time.monotonic() - self.dock_started) / .32)
            ease = 1 - (1 - progress) ** 3
            if progress >= 1:
                self.dock_started = None
                return dock
            return tuple(free[i] + (dock[i] - free[i]) * ease for i in range(3))
        return dock if self.docked else free

    def visual_center(self):
        origin_x, origin_y, scale = self.placement()
        return self.pos() + QPoint(round(origin_x + 41 * scale), round(origin_y + 43 * scale))

    def contact_y(self):
        """Widget y of the window edge. The head stays above it; the arms hang below it."""
        scale = self.pet_scale
        return 58 * self.unit - 43 * scale + 72 * scale

    def refresh_mask(self):
        origin_x, origin_y, scale = self.placement()
        body = QRect(int(origin_x - 8), int(origin_y - 6), int(90 * scale + 16), int(96 * scale + 18))
        region = QRegion(body, QRegion.Ellipse)
        perched = bool(self.target) and not self.docked and not self.drag_start
        if perched:
            for paw_x in (16, 48):
                paw = QRect(int(origin_x + paw_x * scale) - 2, int(origin_y + 60 * scale), int(22 * scale) + 4, int(28 * scale))
                region = region.united(QRegion(paw, QRegion.Ellipse))
        if self.mood in ("thinking", "working", "done"):
            badge = QRect(int(origin_x + 56 * scale), int(origin_y - 6 * scale), int(28 * scale), int(28 * scale))
            region = region.united(QRegion(badge, QRegion.Ellipse))
        key = (body.getRect(), self.mood in ("thinking", "working", "done"), perched)
        if key == self._mask_key:
            return
        self._mask_key = key
        self.setMask(region)

    def request_windows(self):
        self.refresh_screen_map()
        own_ids = {int(widget.winId()) for widget in QApplication.topLevelWidgets()}
        self.probe.request(own_ids)

    def refresh_screen_map(self):
        if os.name != "nt":
            return
        from orbit.desktop.win32 import note_qt_screens
        note_qt_screens()

    def on_windows(self, seq, windows):
        if seq != self.probe.seq:
            return
        self.cached_windows = [hydrate_window(window) for window in windows]

    def begin_context(self, window):
        self.context_token += 1
        self.context_pending = True
        token = self.context_token
        snapshot = {key: window.get(key) for key in ("id", "title", "app", "pid", "cwd", "kind")}
        rect = window["rect"]
        snapshot["rect"] = (rect.x(), rect.y(), rect.width(), rect.height())
        threading.Thread(target=self._capture_context, args=(token, window["id"], snapshot), daemon=True).start()

    def _capture_context(self, token, window_id, snapshot):
        try:
            context = capture_context(snapshot)
        except Exception:
            context = {"kind": snapshot.get("kind") or "other", "accent": ACCENTS["other"], "summary": "没能读取这个窗口的路径"}
        self.context_bridge.ready.emit(token, window_id, context)

    def apply_context(self, token, window_id, context):
        if token != self.context_token or not self.target or self.target.get("id") != window_id:
            return
        self.target.update(context)
        self.context_pending = False
        self.chat.set_context(self.target)
        if self.chat.message_layout.count() <= 2:
            self.chat.reset_messages()
            self.chat.append("Codex", self.connection_message(self.target))
        if not self.buried:
            self.overlay.set_target(self.target, False, connected=True)
        if self.pending_message:
            message = self.pending_message
            self.pending_message = None
            self.ask(message)

    def connection_message(self, target):
        path = target.get("path")
        kind = target.get("kind")
        if path and kind == "files":
            names = "、".join(item.rstrip("/") for item in (target.get("entries") or [])[:8])
            listing = f"我看到这些项目：{names}。" if names else ""
            return f"已连接「{target['title']}」。路径是 {path}。{listing}可以直接让我处理这里的文件。"
        if path and kind == "editor":
            read = "我已经读到文件开头。" if target.get("preview") else "这个文件不是可预览的文本。"
            return f"已连接「{target['title']}」。当前文件是 {path}。{read}"
        if path and kind == "terminal":
            return f"已连接终端「{target['title']}」。当前目录是 {path}。"
        if target.get("url"):
            return f"已连接「{target['title']}」。页面地址是 {target['url']}。"
        if path:
            return f"已连接「{target['title']}」。相关路径是 {path}。"
        return f"已连接「{target['title']}」。我认出了{KIND_LABELS.get(kind, '窗口')}，但没有解析出可操作的路径。"

    def set_mood(self, mood):
        if self.mood != mood: self.mood_started = time.monotonic()
        self.mood = mood
        self.update()

    def animate_to(self, point, duration=500):
        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(point)
        self.animation.start()

    def go_home(self):
        self.animate_to(self.home, 560)

    def finish_return(self):
        if not self.target and not self.drag_start and not self.dragged and self.pos() == self.home:
            self.docked = True
            self.dock_started = time.monotonic()
            self.notch.reveal(QApplication.primaryScreen().availableGeometry())
            self.refresh_mask()
            self.update()
            self.raise_()

    def move_chat(self):
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        if self.target:
            rect = self.target["rect"]
            right = rect.right() + 17
            left = rect.left() - self.chat.width() - 17
            y_anchor = rect.top() + 18
        else:
            right = self.x() + self.width() + 12
            left = self.x() - self.chat.width() - 12
            y_anchor = self.y() + self.height() + 12
        x = right if right + self.chat.width() <= area.right() - 8 else left
        x = max(area.left() + 8, min(x, area.right() - self.chat.width() - 8))
        y = max(area.top() + 8, min(y_anchor, area.bottom() - self.chat.height() - 8))
        if not self.chat.is_user_resizing():
            self.chat.move(x, y)

    def place_on_window(self, rect, allow_bounce=False):
        """Move the mascot and the chat together so they stay locked to the window."""
        destination = QPoint(rect.center().x() - self.width() // 2, rect.top() - round(self.contact_y()))
        if self.pos() != destination:
            if allow_bounce and self.bounce:
                self.animate_to(destination, 420)
            else:
                self.animation.stop()
                self.move(destination)
        if not self.chat.isVisible():
            return
        opening = self.chat_motion.state() == QAbstractAnimation.Running
        if opening and allow_bounce:
            return
        if opening:
            self.chat_motion.stop()
            self.chat.setWindowOpacity(1)
        self.move_chat()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        origin_x, origin_y, scale = self.placement()
        painter.translate(origin_x, origin_y)
        painter.scale(scale, scale)
        elapsed = time.monotonic() - self.mood_started
        painter.save()
        y, angle, sx, sy = 0., 0., 1., 1.
        wave = lambda duration: (1 - math.cos(elapsed * 2 * math.pi / duration)) / 2
        holding = self.target and not self.docked and (self.grip > 0.08 or abs(self.slip) > 0.45 or abs(self.lean) > 0.45)
        if self.mood == "idle" and not self.docked:
            if holding:
                y = self.slip * 0.85
                angle = self.lean
                sx, sy = 1 + .07 * self.grip, 1 - .1 * self.grip
            elif self.target:
                y, sx, sy = 2 * wave(3.8), 1 + .025 * wave(3.8), 1 - .02 * wave(3.8)
            else:
                y, sx, sy = -6 * wave(3.6), 1 + .035 * wave(3.6), 1 - .02 * wave(3.6)
        elif self.mood == "dragging":
            sx, sy = 1.06 + .04 * wave(.68), .94 + .04 * (1 - wave(.68))
        elif self.mood == "thinking":
            y, sx, sy = -3 * wave(1.1), 1 + .03 * wave(1.1), 1 - .04 * wave(1.1)
        elif self.mood == "working":
            y, sx, sy = 2 * wave(.48), 1 + .04 * wave(.48), 1 - .05 * wave(.48)
        idle_progress = max(0., min(1., 1-(self.idle_until-time.monotonic())/.9))
        idle_wave = math.sin(idle_progress*math.pi) if time.monotonic()<self.idle_until else 0.
        if self.mood == "idle" and self.idle_kind == "hop":
            y -= 6 * idle_wave
            sx += .04 * idle_wave
            sy -= .06 * idle_wave
        frames = None
        progress = 1.
        if self.landing_started is not None:
            progress = (time.monotonic() - self.landing_started) / .78
            frames = [(0,-28,0,.65,1.45),(.27,8,0,1.34,.66),(.48,-12,0,.82,1.25),(.66,4,0,1.13,.85),(.82,-3,0,.94,1.07),(1,0,0,1,1)]
        elif self.mood == "done":
            progress = elapsed / .68
            frames = [(0,0,0,1,1),(.35,-9,0,.94,1.1),(.7,2,0,1.08,.9),(1,0,0,1,1)]
        if frames and progress < 1:
            for a, b in zip(frames, frames[1:]):
                if a[0] <= progress <= b[0]:
                    t = (progress-a[0])/(b[0]-a[0])
                    y, angle, sx, sy = [a[i]+(b[i]-a[i])*t for i in range(1,5)]
                    break
        elif self.landing_started is not None:
            self.landing_started = None
        if holding and self.mood != "idle" and not (frames and progress < 1):
            y += self.slip * 0.6
            angle += self.lean * 0.65
            sx += .05 * self.grip
            sy -= .07 * self.grip
        painter.translate(41, 43+y)
        painter.rotate(angle)
        painter.scale(sx, sy)
        painter.translate(-41,-43)
        if self.landing_started is not None and progress < 1:
            ring = QColor(self.target.get("accent") if self.target else "#8370da")
            ring.setAlpha(int(180 * (1 - progress)))
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.NoBrush)
            grow = 6 + 22 * progress
            painter.drawEllipse(int(11 - grow), int(11 - grow), int(60 + grow * 2), int(60 + grow * 2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2d2d31"))
        lift_left = lift_right = 0
        if self.target and not self.docked:
            tap = (1 - math.cos(elapsed * 2 * math.pi / .48)) / 2 if self.mood == "working" else 0
            lift_left = 5 * tap if self.mood == "working" else 0
            lift_right = 5 * (1 - tap) if self.mood == "working" else 0
            dig = 5 * self.grip + max(0., -self.slip) * 0.45
            spread = 2.6 * self.grip
            painter.drawEllipse(int(16 - spread), int(66 + dig - lift_left), 18, 16)
            painter.drawEllipse(int(48 + spread), int(66 + dig - lift_right), 18, 16)
        painter.drawEllipse(11, 11, 60, 60)
        if self.target and not self.docked:
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
            painter.drawArc(int(20 - spread), int(74 + dig - lift_left), 10, 5, 200 * 16, 140 * 16)
            painter.drawArc(int(52 + spread), int(74 + dig - lift_right), 10, 5, 200 * 16, 140 * 16)
            painter.setPen(Qt.NoPen)
        ear_cycle = time.monotonic() % 6
        ear = 0.
        if self.target and self.mood == "idle" and 5.2 <= ear_cycle <= 5.7:
            ear = math.sin((ear_cycle - 5.2) / .5 * math.pi) * 9
        painter.save()
        painter.translate(21, 15)
        painter.rotate((-38 if self.mood == "dragging" else -26) - ear)
        painter.drawRoundedRect(-8, -13, 16, 25, 7, 7)
        painter.restore()
        painter.save()
        painter.translate(61, 15)
        painter.rotate((38 if self.mood == "dragging" else 26) + ear)
        painter.drawRoundedRect(-8, -13, 16, 25, 7, 7)
        painter.restore()
        painter.setBrush(QColor("white"))
        eye_y = 37 if self.mood != "thinking" else 34
        if self.mood == "done":
            painter.setPen(QPen(QColor("white"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(27, 39, 9, 6, 0, 180 * 16)
            painter.drawArc(47, 39, 9, 6, 0, 180 * 16)
            painter.drawArc(38, 49, 8, 5, 180 * 16, 180 * 16)
            painter.setPen(Qt.NoPen)
        else:
            eye_h = 1 if self.mood == "idle" and self.idle_kind == "blink" and idle_wave > .85 else 8
            painter.drawEllipse(round(27+self.gaze_x), round(eye_y+self.gaze_y), 6, eye_h)
            painter.drawEllipse(round(49+self.gaze_x), round(eye_y+self.gaze_y), 6, eye_h)
        if self.mood in ("thinking", "working", "done"):
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(58, -2, 24, 24)
            painter.setPen(QColor("#6c62a0" if self.mood != "done" else "#4aa882"))
            painter.setFont(QFont("Sans", 11, QFont.Bold))
            painter.drawText(QRect(58, -2, 24, 24), Qt.AlignCenter, {"thinking": "…", "working": "✦", "done": "✓"}[self.mood])
        painter.restore()
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.animation.stop()
            self.drag_start = event.globalPos() - self.pos()
            self.press_point = event.globalPos()
            self.press_center = self.visual_center()
            self.drag_from_home = self.docked
            self.held_id = self.target["id"] if self.target else None
            self.dock_started = None
            self.tether_from = self.press_center
            self.dragged = False
            self.tether.broken = False
            self.tether.release_started = None
            self.tether._snap_pending = False
            if self.drag_from_home:
                self._close_notch_after_snap = False
                self._notch_released = False
                screen = QApplication.screenAt(self.press_center) or QApplication.primaryScreen()
                self.notch.hold_open(screen.availableGeometry())
            self.request_windows()

    def _home_screen(self):
        screen = QApplication.screenAt(self.visual_center()) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _on_tether_snapped(self):
        """Close the perch only after the elastic band has finished breaking."""
        if not self._close_notch_after_snap:
            return
        self._close_notch_after_snap = False
        self._notch_released = True
        self.notch.dismiss()

    def mouseMoveEvent(self, event):
        if self.drag_start and event.buttons() & Qt.LeftButton:
            new_pos = event.globalPos() - self.drag_start
            if (event.globalPos() - (self.pos() + self.drag_start)).manhattanLength() > 4 or self.dragged:
                if not self.drag_from_home:
                    self.notch.hide()
                elif not self._notch_released:
                    self.notch.hold_open(self._home_screen())
                self.dragged = True
                self.docked = False
                self.dock_started = None
                if self.drag_from_home:
                    delta = event.globalPos() - self.press_point
                    distance = math.hypot(delta.x(), delta.y())
                    progress = max(0., min(1., distance / get_config().tether.break_distance))
                    self.drag_growth = .5 + .5 * progress * progress * (3 - 2 * progress)
                    offset = self.visual_center() - self.pos()
                    new_pos = self.press_center + delta - offset
                self.target = None
                self.move(new_pos)
                was_intact = not self.tether.broken
                self.tether.update_points(self.tether_from, self.visual_center())
                if self.drag_from_home and was_intact and self.tether.broken:
                    self._close_notch_after_snap = True
                self.set_mood("dragging")
                pool = self.cached_windows if self.settings.get("attach", True) else []
                self.hover_target = topmost_at(pool, self.visual_center())
                self.overlay.set_target(self.hover_target, self.settings.get("hints", True))
                self.chat.hide()
                self.raise_()
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        was_dragged = self.dragged
        self.dragged = False
        self.drag_start = None
        self.tether_from = None
        self.tether.release(.19)
        self.overlay.set_target(None)
        self.update()
        if was_dragged:
            self.target = self.hover_target
            if self.drag_from_home and self.target:
                self._close_notch_after_snap = True
            self.hover_target = None
            if self.target and self.target["id"] == self.held_id:
                self.bounce = 10
                self.landing_started = time.monotonic()
                self.probe.watch(self.target["id"], self.own_window_ids())
                self.follow_target()
                if self.settings.get("auto_open", True):
                    QTimer.singleShot(260, self.open_chat)
            elif self.target:
                self.cancel_api()
                self.api_history.clear()
                self.bridge.invalidate()
                self.chat.set_context(self.target)
                self.chat.reset_messages()
                self.chat.append("Codex", f"已放在「{self.target['title']}」上，正在读取路径和内容…")
                self.chat.send_button.setEnabled(True)
                self.chat.stop_button.hide()
                self.begin_context(self.target)
                self.bounce = 10
                self.landing_started = time.monotonic()
                self.probe.watch(self.target["id"], self.own_window_ids())
                self.follow_target()
                if self.settings.get("auto_open", True):
                    QTimer.singleShot(260, self.open_chat)
            else:
                self.probe.unwatch()
                self.live_rect = None
                self.context_token += 1
                self.context_pending = False
                self.pending_message = None
                self.cancel_api()
                self.api_history.clear()
                self.bridge.invalidate()
                self.chat.set_context(None)
                self.chat.reset_messages()
                self.chat.append("Codex", "已回到待命状态。你可以直接聊天，或把我拖到另一个窗口。")
                self.chat.send_button.setEnabled(True)
                self.chat.stop_button.hide()
                self.go_home()
            self.set_mood("idle")
            return
        if self.chat.isVisible(): self.chat.hide()
        else: self.open_chat()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("打开对话", self.open_chat)
        menu.addAction("设置", self.open_settings)
        menu.addAction("回到顶部", self.detach)
        menu.addAction("清空对话", self.clear_conversation)
        if self.target:
            menu.addAction("断开窗口", self.detach)
        menu.addSeparator()
        menu.addAction("退出 Orbit", QApplication.instance().quit)
        menu.exec_(event.globalPos())

    def own_window_ids(self):
        return {int(widget.winId()) for widget in QApplication.topLevelWidgets()}

    def apply_cover(self, covered):
        """Hide with the attached window when a higher window covers it."""
        if covered:
            if self.buried:
                return
            self.buried = True
            self.chat_was_open = self.chat.isVisible()
            self.overlay.hide()
            self.chat.hide()
            self.hide()
            return
        if not self.buried:
            return
        self.buried = False
        self.show()
        self.raise_()
        if self.chat_was_open:
            self.chat_was_open = False
            self.open_chat()
        self.update()

    def open_chat(self):
        if self.buried:
            self.chat_was_open = True
            return
        self.move_chat()
        destination = self.chat.pos()
        opening = not self.chat.isVisible()
        if opening:
            self.chat.move(destination.x(), destination.y() - 18)
            self.chat.setWindowOpacity(0)
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.input.setFocus(Qt.ActiveWindowFocusReason)
        QTimer.singleShot(0, self.chat.input.setFocus)
        if opening:
            self.chat_motion.stop()
            self.chat_motion.setDuration(420)
            self.chat_motion.setStartValue(self.chat.pos())
            self.chat_motion.setEndValue(destination)
            self.chat_motion.start()
            self.chat_fade.stop()
            self.chat_fade.setDuration(280)
            self.chat_fade.setStartValue(0)
            self.chat_fade.setEndValue(1)
            self.chat_fade.start()

    def close_chat(self):
        self.chat.hide()
        if self.target: self.detach()

    def clear_conversation(self):
        self.cancel_api()
        self.pending_message = None
        self.api_history.clear()
        self.bridge.invalidate()
        self.chat.clear_messages()
        self.chat.send_button.setEnabled(True)
        self.chat.stop_button.hide()
        self.set_mood("idle")

    def detach(self):
        self.context_token += 1
        self.context_pending = False
        self.pending_message = None
        self.cancel_api()
        self.api_history.clear()
        self.bridge.invalidate()
        self.target = None
        self.live_rect = None
        self.buried = False
        self.chat_was_open = False
        self.show()
        self.probe.unwatch()
        self.grip = self.slip = self.lean = 0.
        self.ride_vx = self.ride_vy = 0.
        self.overlay.set_target(None)
        self.chat.set_context(None)
        self.chat.send_button.setEnabled(True)
        self.chat.stop_button.hide()
        self.chat.hide()
        self.go_home()
        self.set_mood("idle")
        self.update()

    def follow_target(self):
        self.refresh_screen_map()
        self.probe.set_own_ids(self.own_window_ids())
        if not self.target or self.drag_start:
            return
        if not self.cached_windows:
            return
        current = next((window for window in self.cached_windows if window["id"] == self.target["id"]), None)
        if not current:
            self.misses += 1
            if self.misses >= 3:
                self.detach()
            return
        self.misses = 0
        title_changed = current["title"] != self.target.get("title")
        preserved = {key: self.target.get(key) for key in ("path", "entries", "preview", "summary", "accent", "url")}
        self.target = current
        if not title_changed:
            self.target.update({key: value for key, value in preserved.items() if value is not None})
        rect = self.live_rect if self.live_rect is not None else current["rect"]
        self.target["rect"] = rect
        if self.buried:
            if title_changed:
                self.begin_context(self.target)
            return
        self.overlay.set_target(self.target, False, connected=True)
        self.place_on_window(rect, allow_bounce=True)
        if title_changed:
            self.begin_context(self.target)
        self.update()

    def on_window_moved(self, wid, payload):
        if self.drag_start or not self.target or self.target.get("id") != wid:
            return
        if payload is None:
            self.misses += 1
            if self.misses >= 4:
                self.detach()
            return
        rect, covered = payload
        self.misses = 0
        new_rect = QRect(*rect)
        old = self.target.get("rect")
        moved = old is None or old.topLeft() != new_rect.topLeft() or old.size() != new_rect.size()
        self.live_rect = new_rect
        self.target["rect"] = new_rect
        for window in self.cached_windows:
            if window["id"] == wid:
                window["rect"] = QRect(new_rect)
                break
        if covered:
            self.ride_vx = self.ride_vy = 0.
            self.apply_cover(True)
            return
        revealed = self.buried
        self.apply_cover(False)
        if moved and old is not None and not revealed:
            now = time.monotonic()
            dt = max(0.02, now - self.ride_at) if self.ride_at else 0.04
            self.ride_at = now
            self.ride_vx = (new_rect.center().x() - old.center().x()) / dt
            self.ride_vy = (new_rect.top() - old.top()) / dt
        self.overlay.set_target(self.target, False, connected=True)
        if revealed:
            self.overlay.show()
        self.place_on_window(new_rect, allow_bounce=not moved and not revealed)

    def working_directory(self):
        fallback = Path(self.settings.get("cwd") or Path.home()).expanduser()
        if self.target and self.target.get("path"):
            candidate = Path(self.target["path"])
            folder = candidate if candidate.is_dir() else candidate.parent
            try:
                if folder.is_dir() and os.access(folder, os.W_OK):
                    return str(folder.resolve())
            except OSError:
                pass
        if fallback.is_dir():
            return str(fallback)
        return str(Path.home())

    def ask(self, message):
        if self.target and self.context_pending:
            self.pending_message = message
            self.chat.status.setText("正在读取窗口路径…")
            return
        self.set_mood("thinking")
        QTimer.singleShot(500, lambda: self.set_mood("working") if not self.chat.send_button.isEnabled() else None)
        if self.settings.get("provider") == "api":
            self.ask_api(message)
            return
        cwd = self.working_directory()
        if not Path(cwd).is_dir():
            self.on_error("工作目录不存在，请在设置中重新选择。")
            return
        self.bridge.ask(message, cwd, self.target)

    def cancel_api(self):
        self.api_generation += 1
        self.api_inflight = False
        self.api_tool_running = False

    def ask_api(self, message):
        raw = self.settings.get("endpoint", "").strip()
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or not self.settings.get("model"):
            self.on_error("请在设置中填写完整 API 地址和模型名称。")
            return
        self.api_generation += 1
        self.api_round = 0
        self.api_user_message = message
        context = (
            "你是 Orbit 桌面助手，用中文回复。需要查看或修改文件时调用工具，不要假装已经读过或改过。"
            "写入文件和运行命令会先请用户确认。敏感文件会读取失败。浏览器没有正文时，用 fetch_url 读取 http 或 https 地址。\n"
            + context_prompt(self.target, self.working_directory())
        )
        kept = get_config().api.history_messages
        history = self.api_history[-kept:] if kept else []
        messages = [{"role": "system", "content": context}] + history + [{"role": "user", "content": message}]
        self.post_api(messages)

    def post_api(self, messages):
        self.api_messages = messages
        self.api_inflight = True
        body = {"model": self.settings["model"], "messages": messages, "tools": API_TOOLS, "stream": False}
        self.chat.status.setText("API 正在请求…")
        threading.Thread(
            target=self._post_api,
            args=(self.api_generation, self.api_user_message, self.settings.get("endpoint", "").strip(), self.api_token, body),
            daemon=True,
        ).start()

    def _post_api(self, generation, message, url, token, body):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=get_config().api.timeout_seconds) as response:
                payload = response.read()
            self.api_result.emit(generation, {"ok": True, "message": message, "body": payload})
        except Exception as error:
            self.api_result.emit(generation, {"ok": False, "error": api_error_text(error)})

    def api_finished(self, generation, result):
        if generation != self.api_generation:
            return
        self.api_inflight = False
        if not result.get("ok"):
            self.on_error("API 请求失败：" + result.get("error", "未知错误"))
            return
        try:
            data = json.loads(result["body"].decode("utf-8"))
            message_obj = data["choices"][0]["message"] or {}
            tool_calls = message_obj.get("tool_calls") or []
            if tool_calls and self.api_round < get_config().api.max_rounds:
                self.continue_tools(message_obj, tool_calls)
                return
            answer = message_obj.get("content") or ""
            if not isinstance(answer, str) or not answer.strip():
                answer = message_obj.get("reasoning_content") or ""
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("接口返回空回复")
            self.api_history.extend([{"role": "user", "content": result["message"]}, {"role": "assistant", "content": answer}])
            self.on_finished(answer)
            self.chat.status.setText("API 已就绪")
        except (ValueError, KeyError, IndexError, TypeError, UnicodeError) as error:
            self.on_error("API 请求失败：" + str(error))

    def continue_tools(self, message_obj, tool_calls):
        self.api_round += 1
        generation = self.api_generation
        self.api_tool_running = True
        root = self.working_directory()
        blocked = bool(self.target and not self.target.get("path"))
        content = message_obj.get("content") or ""
        assistant = {"role": "assistant", "content": message_obj.get("content"), "tool_calls": tool_calls}
        if message_obj.get("reasoning_content"):
            assistant["reasoning_content"] = message_obj["reasoning_content"]
        if content.strip():
            self.chat.append("Codex", content.strip())
        threading.Thread(
            target=self._run_tool_round,
            args=(generation, root, blocked, assistant, tool_calls),
            daemon=True,
        ).start()

    def _confirm_from_worker(self, title, detail):
        if QThread.currentThread() is self.thread():
            return confirm_action(title, detail, self.chat)
        box = {}
        self._confirm_box = (title, detail, box)
        self.confirm_request.emit()
        return bool(box.get("ok"))

    def _confirm_on_gui(self):
        title, detail, box = self._confirm_box
        box["ok"] = confirm_action(title, detail, self.chat)

    def on_tool_log(self, author, text):
        self.chat.append(author, text)
        self.chat.status.setText(text)
        self.set_mood("working")

    def on_tools_ready(self, generation, assistant, follow):
        if generation != self.api_generation:
            return
        self.api_tool_running = False
        self.post_api(self.api_messages + [assistant] + follow)

    def _run_tool_round(self, generation, root, blocked, assistant, tool_calls):
        follow = []
        for call in tool_calls:
            if self.api_generation != generation:
                self.api_tool_running = False
                return
            function = call.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args if isinstance(raw_args, dict) else {})
            except ValueError:
                output = "工具参数不是合法 JSON。"
            else:
                self.tool_log.emit("工具", tool_label(name, args))
                if blocked and name != "fetch_url":
                    output = "当前窗口还没有解析出目录，已拒绝改用主目录。"
                else:
                    output = run_tool(name, args, root, self._confirm_from_worker)
            follow.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": output})
        if self.api_generation != generation:
            self.api_tool_running = False
            return
        self.tools_ready.emit(generation, assistant, follow)

    def stop_reply(self):
        self.pending_message = None
        kill_running_shell()
        if self.api_inflight or self.api_tool_running:
            self.cancel_api()
            self.on_finished("已停止")
            self.chat.status.setText("已停止")
        else:
            self.bridge.stop()

    def on_error(self, message):
        self.chat.status.setText(message)
        if self.chat.isVisible():
            self.chat.append("错误", message)
        self.chat.streaming = False
        self.chat.send_button.setEnabled(True)
        self.chat.stop_button.hide()
        self.set_mood("idle")

    def on_status(self, message):
        self.chat.status.setText(message)
        if "思考" in message: self.set_mood("thinking")
        elif "回复" in message or "执行" in message: self.set_mood("working")

    def on_finished(self, text):
        self.chat.finish(text)
        self.set_mood("done")
        QTimer.singleShot(1500, lambda: self.set_mood("idle") if self.chat.send_button.isEnabled() else None)

    def open_settings(self):
        dialog = SettingsDialog(self, self.settings)
        if dialog.exec_() != QDialog.Accepted:
            return
        cwd = str(Path(dialog.cwd.text()).expanduser().resolve())
        if not Path(cwd).is_dir():
            QMessageBox.warning(self, "Orbit", "请选择存在的工作目录。")
            return
        self.settings = {"cwd": cwd, "attach": dialog.auto_attach.isChecked(),
                         "auto_open": dialog.auto_open.isChecked(), "hints": dialog.hints.isChecked(),
                         "provider": "api" if dialog.provider.currentIndex() else "codex",
                         "endpoint": dialog.endpoint.text().strip(), "model": dialog.model.text().strip()}
        self.api_token = dialog.token.text().strip()
        save_user_settings(self.settings)
        self.chat.set_backend_label(self.backend_label())
        self.set_autostart(dialog.autostart.isChecked())

    def set_autostart(self, enabled):
        error = set_login_autostart(enabled)
        if error:
            QMessageBox.information(self, "Orbit", error)
