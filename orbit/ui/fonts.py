"""UI fonts that stay sharp on this machine.

The prototype names Noto Sans CJK SC, which is not installed on Windows.
Qt then falls back to a small, poorly hinted face. Microsoft YaHei UI is the
system UI face and already covers Chinese; DirectWrite fills symbols it lacks.
"""

import os

from PyQt5.QtGui import QFont


def ui_families():
    if os.name == "nt":
        return ["Microsoft YaHei UI", "Segoe UI", "Segoe UI Symbol"]
    return ["Noto Sans CJK SC", "Source Han Sans SC", "Sans Serif"]


def ui_font_css():
    return ", ".join(f'"{name}"' for name in ui_families())


def mono_font_css():
    if os.name == "nt":
        return '"Cascadia Mono", Consolas, "Microsoft YaHei UI", monospace'
    return '"Noto Sans Mono CJK SC", monospace'


def ui_font(pixel_size=13, bold=False, subpixel=True):
    font = QFont()
    font.setFamilies(ui_families())
    font.setPixelSize(max(1, int(pixel_size)))
    strategy = QFont.PreferAntialias | QFont.PreferQuality
    if not subpixel:
        strategy |= QFont.NoSubpixelAntialias
    font.setStyleStrategy(strategy)
    font.setHintingPreference(QFont.PreferFullHinting if subpixel else QFont.PreferNoHinting)
    if bold:
        font.setBold(True)
    return font


def apply_app_font(app):
    app.setFont(ui_font(13))
