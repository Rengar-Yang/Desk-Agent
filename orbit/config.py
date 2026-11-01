"""Load Orbit's project config and the user's saved settings."""

import json
import os
import sys
import tomllib
from pathlib import Path


def _source_root():
    return Path(__file__).resolve().parent.parent


def _runtime_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _source_root()


def _config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "orbit-codex"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "orbit-codex"


def _config_file():
    preferred = _runtime_root() / "config.toml"
    if preferred.is_file():
        return preferred
    bundled = _source_root() / "config.toml"
    return bundled if bundled.is_file() else preferred


ROOT = _runtime_root()
CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / "settings.json"
APP_CONFIG_FILE = _config_file()

# The painted mascot was designed at this scale inside a 112×118 canvas.
# config.toml scale is a multiplier on that current appearance.
BASE_PAINT_SCALE = 0.78
BASE_CANVAS = (112, 118)

_CONFIG = None


def _section(data, name):
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _float(section, key, default, low, high):
    try:
        value = float(section.get(key, default))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def _int(section, key, default, low, high):
    try:
        value = int(section.get(key, default))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def _bool(section, key, default):
    value = section.get(key, default)
    return default if not isinstance(value, bool) else value


def _text(section, key, default=""):
    value = section.get(key, default)
    return default if not isinstance(value, str) else value.strip()


class MascotConfig:
    def __init__(self, data):
        self.scale = _float(data, "scale", 1.0, 0.35, 3.0)

    @property
    def paint_scale(self):
        return BASE_PAINT_SCALE * self.scale

    @property
    def canvas_width(self):
        return round(BASE_CANVAS[0] * self.scale)

    @property
    def canvas_height(self):
        return round(BASE_CANVAS[1] * self.scale)


class ChatConfig:
    def __init__(self, data):
        self.width = _int(data, "width", 374, 240, 1600)
        self.height = _int(data, "height", 494, 280, 1600)
        self.min_width = _int(data, "min_width", 320, 200, 1200)
        self.min_height = _int(data, "min_height", 420, 240, 1400)
        self.max_width = _int(data, "max_width", 820, self.min_width, 2400)
        self.max_height = _int(data, "max_height", 900, self.min_height, 2400)
        self.width = min(self.max_width, max(self.min_width, self.width))
        self.height = min(self.max_height, max(self.min_height, self.height))


class TetherConfig:
    def __init__(self, data):
        self.break_distance = _int(data, "break_distance", 220, 80, 800)


class ApiConfig:
    def __init__(self, data):
        provider = _text(data, "provider", "codex").lower()
        self.provider = provider if provider in ("codex", "api") else "codex"
        self.endpoint = _text(data, "endpoint", "")
        self.model = _text(data, "model", "")
        self.timeout_seconds = _int(data, "timeout_seconds", 90, 5, 600)
        self.token_file = _text(data, "token_file", "api.txt") or "api.txt"
        self.max_rounds = _int(data, "max_rounds", 8, 1, 32)
        self.history_messages = _int(data, "history_messages", 20, 0, 200)


class CodexConfig:
    def __init__(self, data):
        self.watchdog_ms = _int(data, "watchdog_ms", 120000, 5000, 600000)
        self.client_name = _text(data, "client_name", "orbit_desktop") or "orbit_desktop"
        self.client_title = _text(data, "client_title", "Orbit Desktop") or "Orbit Desktop"


class WorkspaceConfig:
    def __init__(self, data):
        self.default_cwd = _text(data, "default_cwd", "")


class BehaviorConfig:
    def __init__(self, data):
        self.attach = _bool(data, "attach", True)
        self.auto_open = _bool(data, "auto_open", True)
        self.hints = _bool(data, "hints", True)
        self.follow_ms = _int(data, "follow_ms", 200, 40, 5000)
        self.scan_ms = _int(data, "scan_ms", 700, 100, 10000)


class ContextConfig:
    def __init__(self, data):
        self.preview_chars = _int(data, "preview_chars", 6000, 200, 50000)
        self.selected_preview_chars = _int(data, "selected_preview_chars", 4000, 200, 50000)
        self.directory_limit = _int(data, "directory_limit", 60, 1, 500)


class ToolsConfig:
    def __init__(self, data):
        self.shell_timeout = _int(data, "shell_timeout", 120, 1, 300)
        self.fetch_timeout = _int(data, "fetch_timeout", 20, 1, 120)
        self.list_limit = _int(data, "list_limit", 200, 1, 1000)


class AppInfo:
    def __init__(self, data):
        self.name = _text(data, "name", "Orbit") or "Orbit"
        self.version = _text(data, "version", "0.1.0") or "0.1.0"


class OrbitConfig:
    def __init__(self, data):
        data = data if isinstance(data, dict) else {}
        self.raw = data
        self.app = AppInfo(_section(data, "app"))
        self.mascot = MascotConfig(_section(data, "mascot"))
        self.chat = ChatConfig(_section(data, "chat"))
        self.tether = TetherConfig(_section(data, "tether"))
        self.api = ApiConfig(_section(data, "api"))
        self.codex = CodexConfig(_section(data, "codex"))
        self.workspace = WorkspaceConfig(_section(data, "workspace"))
        self.behavior = BehaviorConfig(_section(data, "behavior"))
        self.context = ContextConfig(_section(data, "context"))
        self.tools = ToolsConfig(_section(data, "tools"))


def load_app_config():
    data = {}
    try:
        with APP_CONFIG_FILE.open("rb") as handle:
            loaded = tomllib.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"Orbit 配置无法读取，已使用内置默认值：{error}", flush=True)
    return OrbitConfig(data)


def get_config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_app_config()
    return _CONFIG


def load_api_token():
    """Read a bearer token from the configured file, then the user config dir."""
    token_path = Path(get_config().api.token_file).expanduser()
    if not token_path.is_absolute():
        token_path = APP_CONFIG_FILE.parent / token_path
    # The key saved from Settings lives in the user config dir and wins over
    # a token file shipped beside the program.
    candidates = [CONFIG_DIR / token_path.name, token_path]
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        token = next((line.strip() for line in lines if line.strip()), "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            return token
    return ""


def default_user_settings():
    cfg = get_config()
    cwd = cfg.workspace.default_cwd
    if not cwd or not Path(cwd).expanduser().is_dir():
        cwd = str(Path.home())
    else:
        cwd = str(Path(cwd).expanduser())
    return {
        "cwd": cwd,
        "attach": cfg.behavior.attach,
        "auto_open": cfg.behavior.auto_open,
        "hints": cfg.behavior.hints,
        "provider": cfg.api.provider,
        "endpoint": cfg.api.endpoint,
        "model": cfg.api.model,
    }


def load_user_settings():
    """Project defaults, then values saved from the settings dialog."""
    settings = default_user_settings()
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if isinstance(stored, dict):
        settings.update(stored)
    if settings.get("provider") not in ("codex", "api"):
        settings["provider"] = default_user_settings()["provider"]
    return settings


def setting_int(settings, key, default, low, high):
    try:
        value = int(settings.get(key))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def setting_float(settings, key, default, low, high):
    try:
        value = float(settings.get(key))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def mascot_scale(settings):
    if settings.get("scale") in (None, ""):
        return get_config().mascot.scale
    return setting_float(settings, "scale", get_config().mascot.scale, 0.35, 3.0)


def follow_interval(settings):
    if settings.get("follow_ms") in (None, ""):
        return get_config().behavior.follow_ms
    return setting_int(settings, "follow_ms", get_config().behavior.follow_ms, 40, 2000)


def shell_timeout_seconds(settings=None):
    settings = load_user_settings() if settings is None else settings
    if settings.get("shell_timeout") in (None, ""):
        return get_config().tools.shell_timeout
    return setting_int(settings, "shell_timeout", get_config().tools.shell_timeout, 5, 300)


def save_user_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_api_token(token):
    """Store the API key outside the install folder so upgrades do not touch it."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / "api.txt"
    text = (token or "").strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    if not text:
        path.unlink(missing_ok=True)
        return
    path.write_text(text + "\n", encoding="utf-8")
