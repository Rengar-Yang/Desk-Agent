"""Poll desktop windows off the UI thread so the mascot keeps its frame time."""

import threading
import time

from PyQt5.QtCore import QObject, pyqtSignal

from orbit.desktop.session import query_window_rect, query_windows, window_is_covered


class WindowProbe(QObject):
    """Poll desktop windows off the UI thread so the mascot animation keeps its frame time."""

    found = pyqtSignal(int, object)
    placed = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._busy = False
        self._again = None
        self.seq = 0
        self._watch = None
        self._watch_running = False
        self._own_ids = set()

    def request(self, own_ids):
        with self._lock:
            self._own_ids = set(own_ids)
            self.seq += 1
            job = (self.seq, set(own_ids))
            if self._busy:
                self._again = job
                return
            self._busy = True
        threading.Thread(target=self._work, args=job, daemon=True).start()

    def set_own_ids(self, own_ids):
        with self._lock:
            self._own_ids = set(own_ids)

    def watch(self, wid, own_ids=None):
        with self._lock:
            self._watch = str(wid)
            if own_ids is not None:
                self._own_ids = set(own_ids)
            if self._watch_running:
                return
            self._watch_running = True
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def unwatch(self):
        with self._lock:
            self._watch = None

    def _watch_loop(self):
        last = None
        seen = None
        miss_sent = False
        covered = False
        checked = 0.
        cache = {}
        while True:
            with self._lock:
                wid = self._watch
                own_ids = set(self._own_ids)
                if wid is None:
                    if self._watch is None:
                        self._watch_running = False
                        return
                    wid = self._watch
                    own_ids = set(self._own_ids)
            if wid != seen:
                seen = wid
                last = None
                covered = False
                checked = 0.
                cache = {}
                miss_sent = False
            try:
                rect = query_window_rect(wid)
            except Exception:
                rect = None
            if rect is None:
                if not miss_sent:
                    miss_sent = True
                    last = None
                    self.placed.emit(wid, None)
            else:
                miss_sent = False
                now = time.monotonic()
                if now - checked > 0.2:
                    checked = now
                    try:
                        covered = window_is_covered(wid, rect, own_ids, cache)
                    except Exception:
                        covered = False
                state = (rect, covered)
                if state != last:
                    last = state
                    self.placed.emit(wid, state)
            time.sleep(0.04)

    def _work(self, seq, own_ids):
        try:
            windows = query_windows(own_ids)
        except Exception:
            windows = []
        self.found.emit(seq, windows)
        with self._lock:
            again = self._again
            self._again = None
            if again is None:
                self._busy = False
                return
        threading.Thread(target=self._work, args=again, daemon=True).start()
