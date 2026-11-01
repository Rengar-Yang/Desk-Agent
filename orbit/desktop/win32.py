"""Enumerate and measure top-level windows on Windows."""

import ctypes
import os
import re
import threading
from ctypes import wintypes
from urllib.parse import unquote, urlparse

from orbit.desktop.common import classify_window, is_overlay_process, window_covers

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
if not hasattr(ctypes, "HRESULT"):
    ctypes.HRESULT = ctypes.c_long

SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "XamlExplorerHostIslandWindow",
}
# Recording / capture surfaces that often sit fullscreen above Explorer.
CAPTURE_EXACT = {
    "obs64.exe", "obs32.exe", "obs.exe",
    "bdcam.exe", "bandicam.exe",
    "fraps.exe",
    "medal.exe", "medalencoder.exe",
    "streamlabs obs.exe", "streamlabs obs64.exe",
    "xsplit.core.exe", "xsplit.gamecaster.exe",
    "action.exe",  # Mirillis Action
    "nvidia share.exe", "nvidia overlay.exe",
}
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
CLASS_KIND = {
    "CabinetWClass": "files",
    "ExploreWClass": "files",
    "ConsoleWindowClass": "terminal",
    "CASCADIA_HOSTING_WINDOW_CLASS": "terminal",
}
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
COINIT_APARTMENTTHREADED = 0x2
CLSCTX_ALL = 23
VT_I4 = 3
VT_BSTR = 8
VT_DISPATCH = 9
DISPATCH_METHOD = 1
DISPATCH_PROPERTYGET = 2
LOCALE_USER_DEFAULT = 0x0400

_user32 = None
_kernel32 = None
_dwmapi = None
_ntdll = None
_ole32 = None
_oleaut32 = None
_convert_dpi = False
_screens = []
_com = threading.local()


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


IID_NULL = GUID()


class VARIANT(ctypes.Structure):
    class DATA(ctypes.Union):
        _fields_ = [
            ("llVal", ctypes.c_longlong),
            ("lVal", ctypes.c_long),
            ("bstrVal", ctypes.c_void_p),
            ("pdispVal", ctypes.c_void_p),
            ("decimal", ctypes.c_byte * 16),
        ]

    _anonymous_ = ("data",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("data", DATA),
    ]


class DISPPARAMS(ctypes.Structure):
    _fields_ = [
        ("rgvarg", ctypes.POINTER(VARIANT)),
        ("rgdispidNamedArgs", ctypes.POINTER(ctypes.c_long)),
        ("cArgs", ctypes.c_uint),
        ("cNamedArgs", ctypes.c_uint),
    ]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2_0", ctypes.c_void_p),
        ("Reserved2_1", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


WNDENUMPROC = _WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = _WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM,
)
_GetIDs = _WINFUNCTYPE(
    ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_wchar_p),
    ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(ctypes.c_long),
)
_Invoke = _WINFUNCTYPE(
    ctypes.HRESULT, ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_ulong,
    ctypes.c_ushort, ctypes.POINTER(DISPPARAMS), ctypes.POINTER(VARIANT),
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint),
)
_Release = _WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def user32():
    global _user32
    if _user32 is None:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        _user32.EnumWindows.restype = wintypes.BOOL
        _user32.IsWindow.argtypes = [wintypes.HWND]
        _user32.IsWindow.restype = wintypes.BOOL
        _user32.IsWindowVisible.argtypes = [wintypes.HWND]
        _user32.IsWindowVisible.restype = wintypes.BOOL
        _user32.IsIconic.argtypes = [wintypes.HWND]
        _user32.IsIconic.restype = wintypes.BOOL
        _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        _user32.GetWindowTextLengthW.restype = ctypes.c_int
        _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _user32.GetWindowTextW.restype = ctypes.c_int
        _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _user32.GetClassNameW.restype = ctypes.c_int
        _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        _user32.GetWindowRect.restype = wintypes.BOOL
        _user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
        _user32.EnumDisplayMonitors.restype = wintypes.BOOL
        _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
        _user32.GetMonitorInfoW.restype = wintypes.BOOL
        long_ptr = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
        long_ptr.argtypes = [wintypes.HWND, ctypes.c_int]
        long_ptr.restype = ctypes.c_size_t
        _user32._orbit_get_long = long_ptr
    return _user32


def kernel32():
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _kernel32.OpenProcess.restype = wintypes.HANDLE
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        _kernel32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        ]
        _kernel32.ReadProcessMemory.restype = wintypes.BOOL
        _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        _kernel32.Process32FirstW.restype = wintypes.BOOL
        _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        _kernel32.Process32NextW.restype = wintypes.BOOL
    return _kernel32


def dwmapi():
    global _dwmapi
    if _dwmapi is None:
        _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        _dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT
    return _dwmapi


def ntdll():
    global _ntdll
    if _ntdll is None:
        _ntdll = ctypes.WinDLL("ntdll")
        _ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p,
        ]
        _ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    return _ntdll


def ole32():
    global _ole32
    if _ole32 is None:
        _ole32 = ctypes.WinDLL("ole32")
        _ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
        _ole32.CLSIDFromString.restype = ctypes.HRESULT
        _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        _ole32.CoInitializeEx.restype = ctypes.HRESULT
        _ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        _ole32.CoCreateInstance.restype = ctypes.HRESULT
    return _ole32


def oleaut32():
    global _oleaut32
    if _oleaut32 is None:
        _oleaut32 = ctypes.WinDLL("oleaut32")
        _oleaut32.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]
        _oleaut32.VariantClear.restype = ctypes.HRESULT
    return _oleaut32


def prepare_dpi(qt, application):
    """Make Win32 coordinates and Qt coordinates describe the same pixels."""
    global _convert_dpi
    aware = False
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.HRESULT
        shcore.SetProcessDpiAwareness(2)
        shcore.GetProcessDpiAwareness.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
        shcore.GetProcessDpiAwareness.restype = ctypes.HRESULT
        level = ctypes.c_int()
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(level)) == 0 and level.value > 0:
            aware = True
    except OSError:
        try:
            aware = bool(ctypes.windll.user32.IsProcessDPIAware())
        except OSError:
            aware = False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Orbit.Desktop.Codex")
    except OSError:
        pass
    _convert_dpi = aware
    if aware:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
        application.setAttribute(qt.AA_EnableHighDpiScaling, True)
        application.setAttribute(qt.AA_UseHighDpiPixmaps, True)
    else:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"


def note_qt_screens():
    """Record each monitor's physical and Qt rectangles. Call this on the UI thread."""
    global _screens
    if not _convert_dpi:
        _screens = []
        return
    from PyQt5.QtWidgets import QApplication
    physical = _physical_monitors()
    found = []
    app = QApplication.instance()
    if app is None:
        _screens = []
        return
    for screen in app.screens():
        geo = screen.geometry()
        found.append({
            "name": screen.name(),
            "logical": (geo.x(), geo.y(), geo.width(), geo.height()),
            "physical": physical.get(screen.name()),
            "ratio": float(screen.devicePixelRatio() or 1),
        })
    _screens = found


def _physical_monitors():
    found = {}

    @MONITORENUMPROC
    def callback(monitor, hdc, rect, data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32().GetMonitorInfoW(monitor, ctypes.byref(info)):
            box = info.rcMonitor
            found[info.szDevice] = (box.left, box.top, box.right - box.left, box.bottom - box.top)
        return True

    user32().EnumDisplayMonitors(None, None, callback, 0)
    return found


def to_logical(x, y, width, height):
    screens = _screens
    if not screens:
        return (x, y, width, height)
    for screen in screens:
        physical = screen.get("physical")
        if not physical:
            continue
        px, py, pw, ph = physical
        if pw <= 0 or ph <= 0 or not (px <= x < px + pw and py <= y < py + ph):
            continue
        lx, ly, lw, lh = screen["logical"]
        return (
            round(lx + (x - px) * lw / pw),
            round(ly + (y - py) * lh / ph),
            max(1, round(width * lw / pw)),
            max(1, round(height * lh / ph)),
        )
    ratio = screens[0].get("ratio") or 1
    if ratio == 1:
        return (x, y, width, height)
    return (round(x / ratio), round(y / ratio), max(1, round(width / ratio)), max(1, round(height / ratio)))


def _hwnd(value):
    return int(value) & 0xFFFFFFFFFFFFFFFF


def _wid(hwnd):
    return format(_hwnd(hwnd), "#x")


def _text(hwnd):
    length = user32().GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32().GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    if not user32().GetClassNameW(hwnd, buffer, 256):
        return ""
    return buffer.value


def _cloaked(hwnd):
    value = wintypes.DWORD()
    if dwmapi().DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) != 0:
        return False
    return bool(value.value)


def _window_box(hwnd):
    rect = RECT()
    if dwmapi().DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        if not user32().GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 80 or height < 60:
        return None
    return to_logical(rect.left, rect.top, width, height)


def _pid(hwnd):
    value = wintypes.DWORD()
    user32().GetWindowThreadProcessId(hwnd, ctypes.byref(value))
    return int(value.value)


def _open_process(pid, access):
    handle = kernel32().OpenProcess(access, False, pid)
    return handle or None


def _image_path(pid):
    handle = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32().QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32().CloseHandle(handle)


def _read_bytes(handle, address, size):
    buffer = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t()
    ok = kernel32().ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read))
    if not ok or read.value < size:
        return None
    return bytes(buffer)


def _read_pointer(handle, address):
    raw = _read_bytes(handle, address, 8)
    if raw is None:
        return None
    return int.from_bytes(raw, "little")


def _read_unicode(handle, address):
    header = _read_bytes(handle, address, 16)
    if header is None:
        return None
    length = int.from_bytes(header[0:2], "little")
    pointer = int.from_bytes(header[8:16], "little")
    if length <= 0 or length > 8192 or not pointer:
        return None
    raw = _read_bytes(handle, pointer, length)
    if raw is None:
        return None
    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")


def _tidy_dir(path):
    if not path:
        return None
    path = path.strip().strip('"')
    if path.startswith("\\\\?\\"):
        path = path[4:]
    if len(path) == 2 and path[1] == ":":
        path += "\\"
    else:
        path = path.rstrip("\\/")
    return path or None


def _process_strings(pid):
    """Return the current directory and command line stored in a 64-bit process."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None, ""
    handle = _open_process(pid, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
    if not handle:
        return None, ""
    try:
        info = PROCESS_BASIC_INFORMATION()
        status = ntdll().NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None)
        if status != 0 or not info.PebBaseAddress:
            return None, ""
        peb = int(info.PebBaseAddress)
        params = _read_pointer(handle, peb + 0x20)
        if not params:
            return None, ""
        cwd = _tidy_dir(_read_unicode(handle, params + 0x38))
        command = _read_unicode(handle, params + 0x70) or ""
        return cwd, command
    finally:
        kernel32().CloseHandle(handle)


def kind_for(app, class_name, title):
    if class_name in CLASS_KIND and not app:
        return CLASS_KIND[class_name]
    if app:
        kind = classify_window(app)
        if kind != "other":
            return kind
    lowered = (title or "").lower()
    if lowered.endswith(" - cursor") or "visual studio code" in lowered or lowered.endswith(" - notepad"):
        return "editor"
    if class_name in CLASS_KIND:
        return CLASS_KIND[class_name]
    if "chrome_widgetwin" in class_name.lower():
        return "browser"
    return classify_window(class_name)


def display_title(title, kind):
    if kind == "files":
        title = re.sub(r"\s+[—–-]\s+(File Explorer|文件资源管理器)$", "", title or "", flags=re.IGNORECASE).strip()
    return title or "窗口"


def explorer_location_to_path(url):
    if not url:
        return None
    if url.lower().startswith("file:"):
        path = unquote(urlparse(url).path)
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        path = path.replace("/", "\\")
        return _tidy_dir(path)
    return _tidy_dir(url)


def _guid(text):
    value = GUID()
    if ole32().CLSIDFromString(text, ctypes.byref(value)) != 0:
        raise OSError(text)
    return value


def _ensure_com():
    if getattr(_com, "ok", False):
        return True
    hr = ole32().CoInitializeEx(None, COINIT_APARTMENTTHREADED) & 0xFFFFFFFF
    if hr not in (0, 1, 0x80010106):
        return False
    _com.ok = True
    return True


def _as_this(pointer):
    if isinstance(pointer, int):
        return ctypes.c_void_p(pointer)
    return pointer


def _vtable(pointer):
    this = _as_this(pointer)
    address = int(this.value or 0)
    if not address:
        raise OSError("空的 COM 对象")
    return ctypes.cast(this, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _fn(pointer, index, prototype):
    slot = _vtable(pointer)[index]
    address = slot if isinstance(slot, int) else ctypes.cast(slot, ctypes.c_void_p).value
    return prototype(int(address))


def _release(pointer):
    this = _as_this(pointer)
    if this and this.value:
        _fn(this, 2, _Release)(this)


def _dispid(pointer, name):
    this = _as_this(pointer)
    names = (ctypes.c_wchar_p * 1)(name)
    found = ctypes.c_long()
    hr = _fn(this, 5, _GetIDs)(
        this, ctypes.byref(IID_NULL), names, 1, LOCALE_USER_DEFAULT, ctypes.byref(found),
    )
    if hr < 0:
        raise OSError(name)
    return found.value


def _invoke(pointer, name, flags, args=()):
    this = _as_this(pointer)
    variants = (VARIANT * len(args))()
    for index, value in enumerate(reversed(args)):
        variants[index].vt = VT_I4
        variants[index].lVal = int(value)
    params = DISPPARAMS()
    if args:
        params.rgvarg = ctypes.cast(variants, ctypes.POINTER(VARIANT))
        params.cArgs = len(args)
    result = VARIANT()
    hr = _fn(this, 6, _Invoke)(
        this, _dispid(this, name), ctypes.byref(IID_NULL), LOCALE_USER_DEFAULT,
        flags, ctypes.byref(params), ctypes.byref(result), None, None,
    )
    if hr < 0:
        raise OSError(name)
    return result


def _variant_text(result):
    if result.vt != VT_BSTR or not result.bstrVal:
        return ""
    return ctypes.wstring_at(result.bstrVal)


def _variant_int(result):
    if result.vt in (VT_I4, 19, 22):
        return int(result.lVal)
    if result.vt == 20:
        return int(result.llVal)
    return 0


def _create(clsid):
    unknown = ctypes.c_void_p()
    dispatch = _guid("{00020400-0000-0000-C000-000000000046}")
    hr = ole32().CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(dispatch), ctypes.byref(unknown))
    if hr < 0 or not unknown.value:
        raise OSError("CoCreateInstance")
    return unknown


_folder_cache = (0.0, {})


def explorer_folders():
    """Map Explorer window handles to the folder they are showing."""
    global _folder_cache
    import time
    now = time.monotonic()
    if now - _folder_cache[0] < 1.0:
        return _folder_cache[1]
    found = _read_explorer_folders()
    _folder_cache = (now, found)
    return found


def _read_explorer_folders():
    if not _ensure_com():
        return {}
    shell = None
    listed = None
    try:
        shell = _create(_guid("{13709620-C279-11CE-A49E-444553540000}"))
        listed = _invoke(shell, "Windows", DISPATCH_METHOD | DISPATCH_PROPERTYGET)
        windows = listed.pdispVal if listed.vt == VT_DISPATCH else None
        if not windows:
            return {}
        count = _invoke(windows, "Count", DISPATCH_PROPERTYGET)
        total = _variant_int(count)
        oleaut32().VariantClear(ctypes.byref(count))
        found = {}
        for index in range(max(0, total)):
            try:
                item = _invoke(windows, "Item", DISPATCH_METHOD, (index,))
            except (OSError, ctypes.ArgumentError, ValueError):
                continue
            window = item.pdispVal if item.vt == VT_DISPATCH else None
            if not window:
                oleaut32().VariantClear(ctypes.byref(item))
                continue
            try:
                handle = _invoke(window, "HWND", DISPATCH_PROPERTYGET)
                location = _invoke(window, "LocationURL", DISPATCH_PROPERTYGET)
                hwnd = _variant_int(handle)
                path = explorer_location_to_path(_variant_text(location))
                oleaut32().VariantClear(ctypes.byref(handle))
                oleaut32().VariantClear(ctypes.byref(location))
                if hwnd and path:
                    found[int(hwnd) & 0xFFFFFFFF] = path
            except (OSError, ctypes.ArgumentError, ValueError):
                pass
            finally:
                oleaut32().VariantClear(ctypes.byref(item))
        return found
    except (OSError, ctypes.ArgumentError, ValueError):
        return {}
    finally:
        if listed is not None:
            oleaut32().VariantClear(ctypes.byref(listed))
        _release(shell)


def _own_process(pid, image):
    if pid == os.getpid():
        return True
    return os.path.basename(image or "").lower() == "orbit.exe"


def _exstyle(hwnd):
    try:
        return int(user32()._orbit_get_long(hwnd, GWL_EXSTYLE))
    except Exception:
        return 0


def _monitor_rects_logical():
    screens = _screens
    if screens:
        return [screen["logical"] for screen in screens if screen.get("logical")]
    found = []
    for _name, physical in _physical_monitors().items():
        found.append(to_logical(*physical))
    if found:
        return found
    try:
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        if width > 0 and height > 0:
            return [(0, 0, width, height)]
    except Exception:
        pass
    return []


def _max_monitor_coverage(box):
    x, y, width, height = box
    if width <= 0 or height <= 0:
        return 0.0
    best = 0.0
    for mx, my, mw, mh in _monitor_rects_logical():
        if mw <= 0 or mh <= 0:
            continue
        left, top = max(x, mx), max(y, my)
        right, bottom = min(x + width, mx + mw), min(y + height, my + mh)
        inter = max(0, right - left) * max(0, bottom - top)
        best = max(best, inter / float(mw * mh))
    return best


def _is_ignored_layer(hwnd, image="", title="", box=None):
    """Skip GPU overlays, click-through HUDs, and fullscreen capture surfaces."""
    if is_overlay_process(image):
        return True
    style = _exstyle(hwnd)
    if style & WS_EX_TRANSPARENT:
        return True
    if box is None:
        return False
    coverage = _max_monitor_coverage(box)
    overlayish = bool(style & (WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE))
    layered = bool(style & WS_EX_LAYERED)
    base = os.path.basename(image or "").lower()
    if coverage >= 0.9 and (overlayish or (layered and not (title or "").strip())):
        return True
    if coverage >= 0.9 and base in CAPTURE_EXACT and (overlayish or layered):
        return True
    if (style & WS_EX_TOOLWINDOW) and not (title or "").strip() and coverage >= 0.5:
        return True
    lowered = (title or "").lower()
    if coverage >= 0.85 and any(
        token in lowered
        for token in (
            "nvidia overlay", "nvidia geforce", "geforce overlay", "geforce experience",
            "xbox game bar", "game bar", "fullscreen projector", "windowed projector",
        )
    ):
        return True
    return False


def _stacking():
    found = []

    @WNDENUMPROC
    def callback(hwnd, lparam):
        found.append(int(hwnd))
        return True

    user32().EnumWindows(callback, 0)
    return found


def _describe(hwnd, own_ids, folders):
    if not user32().IsWindowVisible(hwnd) or user32().IsIconic(hwnd) or _cloaked(hwnd):
        return None
    number = _hwnd(hwnd)
    if number in own_ids:
        return None
    class_name = _class_name(hwnd)
    if class_name in SKIP_CLASSES:
        return None
    pid = _pid(hwnd)
    image = _image_path(pid)
    if _own_process(pid, image):
        return None
    title = _text(hwnd)
    if title.startswith("@!"):
        return None
    kind = kind_for(image, class_name, title)
    if kind == "overlay":
        return None
    if not title and class_name not in CLASS_KIND:
        return None
    box = _window_box(hwnd)
    if box is None:
        return None
    if _is_ignored_layer(hwnd, image, title, box):
        return None
    cwd, _command = _process_strings(pid)
    folder = folders.get(number & 0xFFFFFFFF)
    described = {
        "id": _wid(number),
        "title": display_title(title, kind),
        "app": image or class_name or "应用",
        "pid": pid,
        "cwd": cwd,
        "rect": box,
        "bounds": box,
        "kind": kind,
    }
    if folder:
        described["folder"] = folder
    return described


def query_windows(own_ids, only_id=None):
    """Return visible top-level windows from bottom to top. Rects are tuples."""
    wanted = None
    if only_id is not None:
        try:
            wanted = int(str(only_id), 16)
        except ValueError:
            return []
    try:
        folders = explorer_folders()
    except Exception:
        folders = {}
    result = []
    for hwnd in reversed(_stacking()):
        number = _hwnd(hwnd)
        if wanted is not None and number != wanted:
            continue
        try:
            described = _describe(hwnd, own_ids, folders)
        except Exception:
            described = None
        if described:
            result.append(described)
    return result


def query_window_rect(wid):
    """Visible rect of one window, or None when it is gone, minimized, or hidden."""
    try:
        hwnd = int(str(wid), 16)
    except ValueError:
        return None
    if not user32().IsWindow(hwnd) or not user32().IsWindowVisible(hwnd) or user32().IsIconic(hwnd) or _cloaked(hwnd):
        return None
    return _window_box(hwnd)


def window_is_covered(wid, rect, own_ids, cache):
    order = _stacking()
    try:
        key = int(str(wid), 16)
    except ValueError:
        return False
    numbers = [_hwnd(hwnd) for hwnd in order]
    if key not in numbers:
        return False
    import time
    now = time.monotonic()
    for other in numbers[:numbers.index(key)]:
        if other in own_ids:
            continue
        cached = cache.get(other)
        if cached is None or now - cached[0] > 0.5:
            try:
                found = None
                if not (_cloaked(other) or user32().IsIconic(other) or not user32().IsWindowVisible(other)):
                    box = _window_box(other)
                    if box and not _is_ignored_layer(other, _image_path(_pid(other)), _text(other), box):
                        found = box
            except Exception:
                found = None
            cached = (now, found)
            cache[other] = cached
        if cached[1] and window_covers(rect, cached[1]):
            return True
    return False


def windows_process_table():
    """Process tree shaped like the Linux reader: pid -> comm, command line, cwd, parent."""
    snapshot = kernel32().CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or int(snapshot) in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return {}, {}
    table = {}
    children = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32().Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            parent = int(entry.th32ParentProcessID)
            comm = entry.szExeFile
            cwd, command = _process_strings(pid)
            table[pid] = {"ppid": parent, "comm": comm, "cmd": command or comm, "cwd": cwd or ""}
            children.setdefault(parent, []).append(pid)
            ok = kernel32().Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32().CloseHandle(snapshot)
    return table, children
