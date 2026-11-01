"""Enumerate and measure X11 windows."""

import ctypes
import os
import re
import subprocess
import time
from pathlib import Path

from orbit.desktop.common import classify_window, is_overlay_process, window_covers

SKIPPED_WINDOW_TYPES = (
    "_NET_WM_WINDOW_TYPE_DESKTOP",
    "_NET_WM_WINDOW_TYPE_DOCK",
    "_NET_WM_WINDOW_TYPE_SPLASH",
    "_NET_WM_WINDOW_TYPE_NOTIFICATION",
    "_NET_WM_WINDOW_TYPE_TOOLTIP",
    "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
    "_NET_WM_WINDOW_TYPE_POPUP_MENU",
    "_NET_WM_WINDOW_TYPE_COMBO",
    "_NET_WM_WINDOW_TYPE_DND",
)


def run_x(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=0.7)
    except (OSError, subprocess.SubprocessError):
        return ""

def query_windows(own_ids, only_id=None):
    """Return managed X11 windows in bottom-to-top order. Rects are tuples."""
    raw = run_x("xprop", "-root", "_NET_CLIENT_LIST_STACKING")
    ids = re.findall(r"0x[0-9a-fA-F]+", raw)
    if only_id:
        ids = [wid for wid in ids if wid == only_id]
    current_desktop = desktop_number(run_x("xprop", "-root", "_NET_CURRENT_DESKTOP"))
    result = []
    for wid in ids:
        if int(wid, 16) in own_ids:
            continue
        props = run_x("xprop", "-id", wid, "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME", "WM_NAME",
                      "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE", "_NET_WM_DESKTOP",
                      "_GTK_FRAME_EXTENTS", "_NET_FRAME_EXTENTS")
        if any(kind in props for kind in SKIPPED_WINDOW_TYPES):
            continue
        if "_NET_WM_STATE_HIDDEN" in props:
            continue
        window_desktop = desktop_number(props)
        if current_desktop is not None and window_desktop not in (None, current_desktop, 0xFFFFFFFF):
            continue
        pid_match = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", props)
        pid = int(pid_match.group(1)) if pid_match else None
        if pid == os.getpid():
            continue
        command = b""
        arguments = []
        if pid:
            try:
                command = (Path("/proc") / str(pid) / "cmdline").read_bytes()
                arguments = [os.fsdecode(arg) for arg in command.split(b"\0") if arg]
                if any(Path(arg).name in ("native_app.py", "orbit-codex") or arg.replace("\\", "/").endswith("/orbit/__main__.py") for arg in arguments):
                    continue
                if any(is_overlay_process(arg) or is_overlay_process(Path(arg).name) for arg in arguments):
                    continue
            except OSError:
                pass
        geo = run_x("xwininfo", "-id", wid)
        if "Map State: IsViewable" not in geo:
            continue
        values = {}
        for key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
            match = re.search(rf"{re.escape(key)}:\s*(-?\d+)", geo)
            if match:
                values[key] = int(match.group(1))
        if len(values) != 4 or values["Width"] < 80 or values["Height"] < 60:
            continue
        title_match = re.search(r'_NET_WM_NAME\([^\n]*? = "((?:\\.|[^"\\\n])*)"', props)
        if not title_match:
            title_match = re.search(r'WM_NAME\([^\n]*? = "((?:\\.|[^"\\\n])*)"', props)
        class_match = re.search(r'WM_CLASS\([^\n]*? = (.+)', props)
        title = unescape_xprop(title_match.group(1)) if title_match else "窗口"
        if title.startswith("@!"):
            continue
        app_class = class_match.group(1).replace('"', "") if class_match else "应用"
        if is_overlay_process(app_class) or classify_window(app_class) == "overlay":
            continue
        lowered_title = (title or "").lower()
        if any(token in lowered_title for token in ("geforce", "nvidia overlay", "game bar")):
            continue
        cwd = None
        if pid:
            try:
                cwd = str((Path("/proc") / str(pid) / "cwd").resolve(strict=True))
            except OSError:
                pass
        result.append({
            "id": wid, "title": title, "app": app_class, "pid": pid, "cwd": cwd,
            "rect": visible_window_rect(values, props),
            "bounds": outer_window_rect(values, props),
            "kind": classify_window(app_class),
        })
    return result

def desktop_number(text):
    match = re.search(r"_NET_(?:CURRENT|WM)_DESKTOP\(CARDINAL\) = (\d+)", text or "")
    return int(match.group(1)) if match else None


def outer_window_rect(values, props):
    """Full window including decorations. This is the layer the user can see."""
    x, y = values["Absolute upper-left X"], values["Absolute upper-left Y"]
    width, height = values["Width"], values["Height"]
    match = re.search(r"_NET_FRAME_EXTENTS\(CARDINAL\)\s*=\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", props)
    if match:
        left, right, top, bottom = map(int, match.groups())
        return (x - left, y - top, width + left + right, height + top + bottom)
    return (x, y, width, height)

def visible_window_rect(values, props):
    """Exclude GTK client shadows; include server-drawn window decorations."""
    x, y = values["Absolute upper-left X"], values["Absolute upper-left Y"]
    width, height = values["Width"], values["Height"]
    for name, sign in (("_GTK_FRAME_EXTENTS", 1), ("_NET_FRAME_EXTENTS", -1)):
        match = re.search(rf"{name}\(CARDINAL\)\s*=\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", props)
        if match:
            left, right, top, bottom = map(int, match.groups())
            candidate = (x + sign * left, y + sign * top, width - sign * (left + right), height - sign * (top + bottom))
            if candidate[2] > 40 and candidate[3] > 40:
                return candidate
    return (x, y, width, height)


def query_window_presence(wid):
    """Return ('gone', None), ('hidden', None), or ('visible', rect)."""
    geo = run_x("xwininfo", "-id", wid)
    if not geo.strip() or "error:" in geo.lower() or "no such window" in geo.lower():
        return "gone", None
    if "Map State: IsViewable" not in geo:
        return "hidden", None
    values = {}
    for key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
        match = re.search(rf"{re.escape(key)}:\s*(-?\d+)", geo)
        if match:
            values[key] = int(match.group(1))
    if len(values) != 4 or values["Width"] < 80 or values["Height"] < 60:
        return "hidden", None
    props = run_x("xprop", "-id", wid, "_GTK_FRAME_EXTENTS", "_NET_FRAME_EXTENTS", "_NET_WM_STATE")
    if "_NET_WM_STATE_HIDDEN" in props:
        return "hidden", None
    return "visible", visible_window_rect(values, props)


def query_window_rect(wid):
    """Visible rect of one window, or None when it is gone or hidden."""
    state, rect = query_window_presence(wid)
    return rect if state == "visible" else None


def unescape_xprop(text):
    if "\\" not in text:
        return text
    try:
        return text.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeError, ValueError):
        return text


def layer_rect(wid, own_ids, current_desktop):
    """Visible rect of a window above the target, ignoring Orbit's own windows."""
    if int(wid, 16) in own_ids:
        return None
    props = run_x(
        "xprop", "-id", wid, "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME", "WM_NAME",
        "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE",
        "_NET_WM_DESKTOP", "_NET_FRAME_EXTENTS", "_GTK_FRAME_EXTENTS",
    )
    if any(kind in props for kind in SKIPPED_WINDOW_TYPES):
        return None
    if "_NET_WM_STATE_HIDDEN" in props:
        return None
    window_desktop = desktop_number(props)
    if current_desktop is not None and window_desktop not in (None, current_desktop, 0xFFFFFFFF):
        return None
    pid_match = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", props)
    if pid_match:
        pid = int(pid_match.group(1))
        if pid == os.getpid():
            return None
        try:
            arguments = [
                os.fsdecode(arg)
                for arg in (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
                if arg
            ]
            if any(is_overlay_process(arg) or is_overlay_process(Path(arg).name) for arg in arguments):
                return None
        except OSError:
            pass
    class_match = re.search(r'WM_CLASS\([^\n]*? = (.+)', props)
    app_class = class_match.group(1).replace('"', "") if class_match else ""
    if is_overlay_process(app_class) or classify_window(app_class) == "overlay":
        return None
    title_match = re.search(r'_NET_WM_NAME\([^\n]*? = "((?:\\.|[^"\\\n])*)"', props)
    if not title_match:
        title_match = re.search(r'WM_NAME\([^\n]*? = "((?:\\.|[^"\\\n])*)"', props)
    title = unescape_xprop(title_match.group(1)) if title_match else ""
    if any(token in title.lower() for token in ("geforce", "nvidia overlay", "game bar")):
        return None
    geo = run_x("xwininfo", "-id", wid)
    if "Map State: IsViewable" not in geo:
        return None
    values = {}
    for key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
        match = re.search(rf"{re.escape(key)}:\s*(-?\d+)", geo)
        if match:
            values[key] = int(match.group(1))
    if len(values) != 4 or values["Width"] < 80 or values["Height"] < 60:
        return None
    return outer_window_rect(values, props)


def window_is_covered(wid, rect, own_ids, cache):
    raw = run_x("xprop", "-root", "_NET_CLIENT_LIST_STACKING", "_NET_CURRENT_DESKTOP")
    ids = re.findall(r"0x[0-9a-fA-F]+", raw or "")
    lowered = [item.lower() for item in ids]
    key = str(wid).lower()
    if key not in lowered:
        return False
    current_desktop = desktop_number(raw)
    now = time.monotonic()
    for other in ids[lowered.index(key) + 1:]:
        cached = cache.get(other)
        if cached is None or now - cached[0] > 0.5:
            try:
                found = layer_rect(other, own_ids, current_desktop)
            except Exception:
                found = None
            cached = (now, found)
            cache[other] = cached
        if cached[1] and window_covers(rect, cached[1]):
            return True
    return False


def _xlib():
    try:
        return ctypes.cdll.LoadLibrary("libX11.so.6")
    except OSError:
        return None


def _prepare_xlib(lib):
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XConfigureWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_void_p]
    lib.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]


def stack_with_window(owner, followers):
    """Place followers immediately above owner, lowest hwnd first."""
    lib = _xlib()
    if lib is None:
        return
    _prepare_xlib(lib)
    display = lib.XOpenDisplay(None)
    if not display:
        return
    try:
        changes = _XWindowChanges()
        changes.stack_mode = 0  # Above
        sibling = int(owner)
        mask = (1 << 5) | (1 << 6)  # CWSibling | CWStackMode
        for hwnd in followers:
            changes.sibling = sibling
            lib.XConfigureWindow(display, int(hwnd), mask, ctypes.byref(changes))
            sibling = int(hwnd)
        lib.XFlush(display)
    except (OSError, TypeError, ValueError):
        return
    finally:
        lib.XCloseDisplay(display)


def float_above(hwnds):
    lib = _xlib()
    if lib is None:
        return
    _prepare_xlib(lib)
    display = lib.XOpenDisplay(None)
    if not display:
        return
    try:
        for hwnd in hwnds:
            lib.XRaiseWindow(display, int(hwnd))
        lib.XFlush(display)
    except (OSError, TypeError, ValueError):
        return
    finally:
        lib.XCloseDisplay(display)


class _XWindowChanges(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("sibling", ctypes.c_ulong),
        ("stack_mode", ctypes.c_int),
    ]


def focus_window(wid):
    """Ask the window manager to raise this window above other normal windows."""
    token = wid if isinstance(wid, str) else hex(int(wid))
    for command in (
        ["xdotool", "windowactivate", token],
        ["wmctrl", "-ia", token],
    ):
        try:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.6, check=False)
            return
        except (OSError, subprocess.SubprocessError):
            continue
