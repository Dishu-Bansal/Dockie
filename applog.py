"""
applog — shared logging for Dockie.

Every module imports this instead of print so that dockie.log captures a
timestamped, thread-safe record of what the app is doing, even when the
app runs without a console (pythonw.exe / windowed exe). Messages are
also mirrored to the console in dev.

Importable from any module without circular imports (stdlib only).
"""

import os
import sys
import threading
import traceback
from datetime import datetime

LOG_PATH = None  # set via configure() once the config dir is known
_lock = threading.Lock()
_handle = None


def configure(path):
    """Point the log at `path` (called by backend once CONFIG_DIR exists)."""
    global LOG_PATH
    LOG_PATH = path


def ensure_open():
    """Open the log file handle. Safe to call repeatedly."""
    global _handle
    if _handle is not None or not LOG_PATH:
        return
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        _handle = open(LOG_PATH, 'a', buffering=1, encoding='utf-8',
                       errors='replace')
    except OSError:
        _handle = open(os.devnull, 'w')


def get_handle():
    """Return the log file handle (for stdout/stderr redirection)."""
    ensure_open()
    return _handle


def log(message, level='INFO'):
    """Write one timestamped line to dockie.log and mirror it to the
    console when there is one. Never raises."""
    line = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level:5s}] {message}'
    with _lock:
        try:
            ensure_open()
            if _handle is not None:
                _handle.write(line + '\n')
        except OSError:
            pass
        # In windowed mode stdout *is* the log handle — printing again
        # would duplicate the line, so only mirror to a real console.
        try:
            if sys.stdout is not None and sys.stdout is not _handle:
                print(line, flush=True)
        except Exception:
            pass


def log_exc(context):
    """Log the current exception (traceback) under `context`."""
    log(f'{context}: {traceback.format_exc().strip()}', level='ERROR')
