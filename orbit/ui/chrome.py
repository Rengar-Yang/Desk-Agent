"""Shared chrome for Orbit menus and floating cards."""

from pathlib import Path

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QColor, QImage, QPainter, QPen
from PyQt5.QtWidgets import QGraphicsDropShadowEffect, QMenu

from orbit.ui.fonts import ui_font, ui_font_css

ACCENT = "#6d62b0"
INK = "#2f2d3a"
MUTED = "#8b879b"
LINE = "#e8e4f0"
CARD = "#fbfaff"
SOFT = "#f3f0f9"
_CHECK_PATH = Path(__file__).with_name("_check.png")


def _check_icon_url():
    if not _CHECK_PATH.exists():
        image = QImage(36, 36, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(ACCENT))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(1, 1, 34, 34, 12, 12)
        pen = QPen(QColor("white"))
        pen.setWidth(4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(9, 19, 15, 26)
        painter.drawLine(15, 26, 27, 11)
        painter.end()
        image.save(str(_CHECK_PATH))
    return QUrl.fromLocalFile(str(_CHECK_PATH.resolve())).toString()


def attach_card_shadow(widget, blur=32, dy=10, alpha=70):
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, dy)
    shadow.setColor(QColor(36, 30, 58, alpha))
    widget.setGraphicsEffect(shadow)
    return shadow


def floating_card_qss(accent=ACCENT):
    return f"""
        #orbitCard {{
            background: {CARD};
            border: 1px solid {LINE};
            border-radius: 20px;
        }}
        QLabel {{
            color: {INK};
            background: transparent;
            font-family: {ui_font_css()};
            font-size: 13px;
        }}
        #orbitTitle {{
            font-size: 18px;
            font-weight: 700;
            color: {INK};
        }}
        #orbitSubtitle, #orbitHint, #orbitNote {{
            color: {MUTED};
            font-size: 12px;
        }}
        #orbitSection {{
            color: {MUTED};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.4px;
            padding-top: 4px;
        }}
        #orbitFieldLabel {{
            color: #5c586e;
            font-size: 12px;
            font-weight: 600;
        }}
        QLineEdit, QComboBox {{
            background: #ffffff;
            border: 1px solid {LINE};
            border-radius: 12px;
            padding: 9px 12px;
            color: {INK};
            font-family: {ui_font_css()};
            font-size: 13px;
            min-height: 18px;
        }}
        QLineEdit:focus, QComboBox:focus {{
            border: 1px solid {accent};
        }}
        QComboBox::drop-down {{
            border: 0;
            width: 28px;
        }}
        QComboBox QAbstractItemView {{
            background: #ffffff;
            border: 1px solid {LINE};
            selection-background-color: {SOFT};
            selection-color: {INK};
            outline: 0;
            padding: 4px;
        }}
        QCheckBox {{
            color: {INK};
            spacing: 10px;
            padding: 10px 12px;
            border-radius: 12px;
            background: {SOFT};
            font-family: {ui_font_css()};
            font-size: 13px;
        }}
        QCheckBox:hover {{
            background: #ebe7f5;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 6px;
            border: 1px solid #d2cce0;
            background: #ffffff;
        }}
        QCheckBox::indicator:checked {{
            background: {accent};
            border-color: {accent};
            image: url("{_check_icon_url()}");
        }}
        QPushButton {{
            border: 0;
            border-radius: 12px;
            padding: 9px 16px;
            font-family: {ui_font_css()};
            font-size: 13px;
            font-weight: 600;
        }}
        #orbitGhostButton {{
            background: {SOFT};
            color: #5c5678;
        }}
        #orbitGhostButton:hover {{
            background: #ebe7f5;
        }}
        #orbitPrimaryButton {{
            background: {accent};
            color: #ffffff;
            min-width: 88px;
        }}
        #orbitPrimaryButton:hover {{
            background: #5f549e;
        }}
        #orbitCloseButton {{
            background: transparent;
            color: {MUTED};
            font-size: 18px;
            font-weight: 500;
            padding: 2px 8px;
            border-radius: 8px;
        }}
        #orbitCloseButton:hover {{
            background: {SOFT};
            color: {INK};
        }}
    """


def menu_qss(accent=ACCENT):
    return f"""
        QMenu {{
            background: {CARD};
            border: 1px solid {LINE};
            border-radius: 14px;
            padding: 8px;
            font-family: {ui_font_css()};
            font-size: 13px;
        }}
        QMenu::item {{
            color: {INK};
            padding: 9px 36px 9px 14px;
            border-radius: 9px;
            margin: 1px 0;
        }}
        QMenu::item:selected {{
            background: {SOFT};
            color: {accent};
        }}
        QMenu::item:disabled {{
            color: #b0abbf;
        }}
        QMenu::separator {{
            height: 1px;
            background: {LINE};
            margin: 6px 10px;
        }}
    """


def make_context_menu(parent):
    menu = QMenu(parent)
    menu.setFont(ui_font(13))
    menu.setWindowFlags(menu.windowFlags() | Qt.NoDropShadowWindowHint | Qt.FramelessWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground, False)
    menu.setStyleSheet(menu_qss())
    return menu
