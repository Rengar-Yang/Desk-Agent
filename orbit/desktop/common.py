"""Window helpers shared by the X11 and Windows desktops."""

from PyQt5.QtCore import QRect

FILES_EXACT = {"explorer.exe", "explorer"}
FILES_PARTS = ("nautilus", "nemo", "thunar", "dolphin", "caja")
TERMINALS_EXACT = {
    "windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "openconsole.exe", "cmd", "powershell", "pwsh",
}
TERMINALS_PARTS = ("terminal", "kitty", "alacritty", "wezterm", "konsole", "ptyxis")
BROWSERS_PARTS = ("firefox", "chrome", "chromium", "brave", "msedge", "opera", "vivaldi")
EDITORS_PARTS = ("cursor", "code", "gedit", "kate", "sublime", "emacs", "texteditor", "notepad")

# Overlay / capture helpers that sit above real windows but are not attach targets.
OVERLAY_EXACT = {
    "nvidia share.exe",
    "nvidia overlay.exe",
    "nvidia geforce experience.exe",
    "geforce experience.exe",
    "gfexperience.exe",
    "nvsphelper64.exe",
    "nvsphelper.exe",
    "nvcontainer.exe",
    "gamebar.exe",
    "gamebarftserver.exe",
    "gameoverlayui.exe",
    "gameoverlayui64.exe",
    "amdow.exe",
    "screenclippinghost.exe",
    "screensketch.exe",
    "textinputhost.exe",
    "rtss.exe",
    "rtsshooksloader64.exe",
    "msi afterburner.exe",
}
OVERLAY_PARTS = (
    "nvidia share",
    "nvidia overlay",
    "nvsphelper",
    "geforce experience",
    "gamebar",
    "gameoverlay",
    "discordoverlay",
    "steamoverlay",
)


def process_basename(app_path):
    app = (app_path or "").lower().replace("\\", "/")
    return app.rsplit("/", 1)[-1]


def is_overlay_process(app_path):
    """True for GPU/recording overlays that must not steal attach or cover checks."""
    base = process_basename(app_path)
    if not base:
        return False
    if base in OVERLAY_EXACT:
        return True
    stem = base[:-4] if base.endswith(".exe") else base
    return any(part in stem or part in base for part in OVERLAY_PARTS)


def classify_window(app_class):
    app = (app_class or "").lower().replace("\\", "/")
    base = app.rsplit("/", 1)[-1]
    if is_overlay_process(app) or is_overlay_process(base):
        return "overlay"
    if base in FILES_EXACT or any(name in app for name in FILES_PARTS):
        return "files"
    if base in TERMINALS_EXACT or any(name in app for name in TERMINALS_PARTS):
        return "terminal"
    if any(name in app for name in BROWSERS_PARTS):
        return "browser"
    if any(name in app for name in EDITORS_PARTS):
        return "editor"
    return "other"


def topmost_at(windows, point):
    """Pick the uppermost visible window under a point. Lower layers stay hidden."""
    for window in reversed(windows):
        if window.get("kind") == "overlay":
            continue
        area = window.get("bounds") or window["rect"]
        if area.contains(point):
            return window
    return None


def hydrate_window(window):
    hydrated = dict(window)
    hydrated["rect"] = QRect(*window["rect"])
    if window.get("bounds"):
        hydrated["bounds"] = QRect(*window["bounds"])
    return hydrated


def window_covers(target, other):
    """True when another window sits above the attached window and hides it."""
    tx, ty, tw, th = target
    ox, oy, ow, oh = other
    if tw < 40 or th < 40 or ow < 80 or oh < 60:
        return False
    left, top = max(tx, ox), max(ty, oy)
    right, bottom = min(tx + tw, ox + ow), min(ty + th, oy + oh)
    if right - left < 80 or bottom - top < 48:
        return False
    perch_x, perch_y = tx + tw / 2, ty + 12
    center_x, center_y = tx + tw / 2, ty + th / 2
    covers_perch = ox <= perch_x <= ox + ow and oy <= perch_y <= oy + oh
    covers_center = ox <= center_x <= ox + ow and oy <= center_y <= oy + oh
    if covers_perch or covers_center:
        return True
    return (right - left) * (bottom - top) > 0.45 * tw * th
