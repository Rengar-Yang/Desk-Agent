"""Desktop windows for the operating system this process is running on."""

import os

from orbit.desktop.common import classify_window, hydrate_window, topmost_at

if os.name == "nt":
    from orbit.desktop.win32 import query_window_rect, query_windows, window_is_covered
else:
    from orbit.desktop.x11 import query_window_rect, query_windows, window_is_covered

__all__ = [
    "classify_window", "hydrate_window", "query_window_rect", "query_windows",
    "topmost_at", "window_is_covered",
]
