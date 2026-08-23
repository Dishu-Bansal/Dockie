"""
Dockie backend — scanning, extraction, file watching, hotkey, system tray,
and PyQt indexing-status window. Launches the Flutter UI on triple-Ctrl
(left or right).
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg

import Updater

from pynput import keyboard

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from scanner import find_pdfs, get_available_roots
from content_extractor import extract_text
import db
import applog
from applog import log, log_exc

# System tray (optional — graceful if pystray/Pillow not installed)
_USE_TRAY = False
try:
    import pystray
    from PIL import Image, ImageDraw
    _USE_TRAY = True
except ImportError:
    pass

# App data always lives in the per-user .dockie dir — never in the install
# folder (Program Files is read-only for non-elevated runs, and an elevated
# first run would otherwise split data across two locations). See db._data_dir().
CONFIG_DIR = db.DATA_DIR
SETTINGS_PATH = os.path.join(CONFIG_DIR, 'settings.json')
LOG_PATH = os.path.join(CONFIG_DIR, 'dockie.log')
applog.configure(LOG_PATH)

if getattr(sys, 'frozen', False):
    # Installed layout: Inno Setup installs dockie_ui.exe next to Dockie.exe.
    FLUTTER_EXE = os.path.join(os.path.dirname(sys.executable), 'dockie_ui.exe')
else:
    # Source layout (dev): dockie_ui\build\windows\x64\runner\Release\dockie_ui.exe
    FLUTTER_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'dockie_ui', 'build', 'windows', 'x64',
                               'runner', 'Release', 'dockie_ui.exe')

# When launched without a console (pythonw.exe, e.g. at login), redirect
# stray prints (third-party output, our own logs) into dockie.log so
# logging never fails on a missing stdout/stderr. applog.log() writes to
# the same handle directly.
if sys.stdout is None or sys.stderr is None:
    # applog.get_handle() creates the dir and falls back to devnull when the
    # config dir is not writable.
    _log = applog.get_handle()
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log


# ── Application state ──
class State:
    def __init__(self):
        self.phase = 'idle'        # idle | scan | extract | done
        self.files_found = 0
        self.files_done = 0
        self.files_empty = 0
        self.current_file = ''
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.shutdown_flag = threading.Event()
        self.extract_wake = threading.Event()
        self.lock = threading.Lock()


_state = State()
_watcher = None  # holds the watchdog Observer once started


# ── File watcher ──
class PdfWatcher(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        self._with_conn(lambda c: db.insert_scan_result(c, event.src_path))
        _state.extract_wake.set()

    def on_modified(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        self._with_conn(lambda c: db.mark_pending(c, event.src_path))
        _state.extract_wake.set()

    def on_moved(self, event):
        if event.is_directory:
            return
        src = event.src_path
        dst = event.dest_path
        src_is_pdf = src.lower().endswith('.pdf')
        dst_is_pdf = dst.lower().endswith('.pdf')

        if src_is_pdf and dst_is_pdf:
            self._with_conn(lambda c: db.move_file(c, src, dst))
            _state.extract_wake.set()
        elif src_is_pdf:
            self._with_conn(lambda c: db.mark_deleted(c, src))
        elif dst_is_pdf:
            self._with_conn(lambda c: db.insert_scan_result(c, dst))
            _state.extract_wake.set()

    def on_deleted(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        self._with_conn(lambda c: db.mark_deleted(c, event.src_path))

    @staticmethod
    def _with_conn(fn):
        c = db.get_conn()
        try:
            fn(c)
            c.commit()
        except Exception:
            log_exc(f'Watcher handler failed ({fn.__name__})')
        finally:
            c.close()


# ── Scan + Extract pipeline ──
def run_scan():
    log('Scan started — walking filesystem for PDFs...')
    conn = db.get_conn()
    try:
        existing = db.get_all_paths(conn)
        log(f'Scan: {len(existing):,} paths already in DB')
        count = 0
        for path in find_pdfs(_state.cancel):
            count += 1
            if path not in existing:
                db.insert_scan_result(conn, path)
                if count % 200 == 0:
                    conn.commit()
            if count % 500 == 0:
                with _state.lock:
                    _state.files_found = count
                log(f'Scan progress: {count:,} files found')
            if _state.cancel.is_set():
                conn.commit()
                log('Scan cancelled')
                return
        conn.commit()
        with _state.lock:
            _state.files_found = count
        log(f'Scan complete: {count:,} total files found')
    except Exception:
        log_exc('Scan failed')
    finally:
        conn.close()


def diff_scan():
    """Reconcile the DB with disk for changes made while the app was shutdown."""
    log('Diff scan started...')
    conn = db.get_conn()
    try:
        db_meta = {
            path: (size, modified)
            for path, size, modified in conn.execute('SELECT path, size, modified FROM files')
        }
        available_roots = set(get_available_roots())

        disk_paths = set()
        new_count = 0
        modified_count = 0
        pending_changes = 0
        last_commit = time.time()

        for path in find_pdfs(_state.cancel):
            if _state.cancel.is_set():
                conn.commit()
                log('Diff scan cancelled')
                return

            # Keep write transactions short so the extractor is never blocked
            # for long; flush any stale pending writes before proceeding.
            if pending_changes and time.time() - last_commit >= 1.0:
                conn.commit()
                _state.extract_wake.set()
                pending_changes = 0
                last_commit = time.time()

            disk_paths.add(path)
            meta = db_meta.get(path)
            if meta is None:
                db.insert_scan_result(conn, path)
                new_count += 1
                pending_changes += 1
            else:
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_size != meta[0] or st.st_mtime != meta[1]:
                    db.mark_pending(conn, path, st.st_size, st.st_mtime)
                    modified_count += 1
                    pending_changes += 1

            if pending_changes >= 200:
                conn.commit()
                _state.extract_wake.set()
                pending_changes = 0
                last_commit = time.time()

        conn.commit()

        deleted_count = 0
        for path in db_meta:
            if path in disk_paths:
                continue
            drive = os.path.splitdrive(path)[0]
            if drive and (drive + '\\') not in available_roots:
                continue
            db.mark_deleted(conn, path)
            deleted_count += 1
            if deleted_count % 200 == 0:
                conn.commit()

        conn.commit()
        _state.extract_wake.set()
        log(f'Diff scan complete: {new_count} new, '
              f'{modified_count} modified, {deleted_count} deleted')
    except Exception:
        log_exc('Diff scan failed')
    finally:
        conn.close()


def run_extract():
    log('Extraction worker started...')
    conn = db.get_conn()
    try:
        while not _state.cancel.is_set():
            if _state.pause.is_set():
                time.sleep(0.2)
                continue
            rows = db.get_pending_batch(conn, limit=1)
            if not rows:
                with _state.lock:
                    _state.files_found = db.get_total_count(conn)
                    _state.files_done = db.get_indexed_count(conn)
                    _state.phase = 'done'
                # Flush WAL into the main .db file so external readers see the
                # latest state without waiting for a restart.
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
                _state.extract_wake.clear()
                if db.get_pending_count(conn) == 0:
                    _state.extract_wake.wait(timeout=1.0)
                continue
            path, filename = rows[0]
            with _state.lock:
                _state.current_file = filename
                if _state.phase == 'done':
                    _state.phase = 'extract'  # new/requeued work arrived after idle
            try:
                text = extract_text(path)
            except Exception as e:
                log(f'Extract failed for {path!r}: {e}', level='WARN')
                text = ''
            db.mark_extracted(conn, path, text)
            conn.commit()
            with _state.lock:
                if not text:
                    _state.files_empty += 1
                # The DB is the source of truth: found = all tracked files,
                # done = files with extracted text. Re-syncing every item keeps
                # the UI honest when new/requeued files arrive after 'done'
                # (otherwise the in-memory counter over-runs the stale total
                # and Done shows bigger than Found).
                _state.files_found = db.get_total_count(conn)
                _state.files_done = db.get_indexed_count(conn)
            if _state.files_done % 50 == 0:
                log(f'Extract progress: {_state.files_done:,}/{_state.files_found:,}')
    except Exception:
        log_exc('Extraction worker failed')
    finally:
        conn.close()
    log(f'Extraction worker stopped: {_state.files_done:,} indexed '
          f'({_state.files_empty:,} empty)')


def pipeline():
    try:
        conn = db.get_conn()
        try:
            db.init_db(conn)
            total = db.get_total_count(conn)
            pending = db.get_pending_count(conn)
        finally:
            conn.close()

        log(f'DB state: {total} total, {pending} pending extraction')

        if total == 0:
            _state.phase = 'scan'
            log('Phase -> scan (fresh start)')
            run_scan()
            if _state.cancel.is_set():
                return
            c = db.get_conn()
            try:
                with _state.lock:
                    _state.files_found = db.get_total_count(c)
                    _state.files_done = db.get_indexed_count(c)
            finally:
                c.close()
        else:
            with _state.lock:
                _state.files_found = total
                _state.files_done = total - pending
            if pending > 0:
                log(f'Resuming: {pending} pending files to index')

        # Start the persistent extractor — it drains the initial backlog now and
        # keeps running to index new/requeued files the watcher adds later.
        _state.phase = 'extract'
        log('Phase -> extract (persistent worker)')
        threading.Thread(target=run_extract, daemon=True).start()

        # Reconcile with disk in the background for changes made while shutdown.
        if total > 0:
            log('Starting background diff scan...')
            threading.Thread(target=diff_scan, daemon=True).start()
    except Exception:
        log_exc('Pipeline failed')


def start_watcher():
    try:
        roots = []
        for r in get_available_roots():
            if os.path.exists(r):
                roots.append(r)
        if not roots:
            log('Watcher: no drive roots found')
            return None
        log(f'Watcher: starting on {len(roots)} drives: {", ".join(roots)}')
        observer = Observer()
        handler = PdfWatcher()
        for r in roots:
            observer.schedule(handler, r, recursive=True)
        observer.start()
        log('Watcher: active')
        return observer
    except Exception:
        log_exc('Watcher failed to start')
        return None


# ── Hotkey (triple-Ctrl) ──
# Three Ctrl presses (left or right) within one second summon the overlay.
# No OS-level suppression is needed (unlike Alt+Space, Ctrl has no system
# side-effect such as the window system menu).
_CTRL_VKS = {0x11, 0xA2, 0xA3}  # VK_CONTROL / VK_LCONTROL / VK_RCONTROL
_last_ctrl_times: list[float] = []
_hotkey_listener = None


def _on_press(key):
    global _last_ctrl_times
    # pynput exposes the VK on KeyCode directly but on Key enum members only
    # via .value.vk (e.g. Key.ctrl_l is <162> with key.vk == None).
    vk = getattr(key, 'vk', None)
    if vk is None:
        vk = getattr(getattr(key, 'value', None), 'vk', None)
    if vk not in _CTRL_VKS:
        _last_ctrl_times.clear()  # any other key resets the triple-Ctrl sequence
        return

    now = time.time()
    _last_ctrl_times.append(now)
    _last_ctrl_times = [t for t in _last_ctrl_times if t > now - 1.0]

    if len(_last_ctrl_times) >= 3:
        _last_ctrl_times.clear()
        log('Hotkey: triple-Ctrl pressed, launching Flutter UI')
        launch_flutter()

# ── Flutter process management ──
_flutter_proc = None  # process handle for the on-demand Flutter UI


def launch_flutter():
    global _flutter_proc
    if not os.path.exists(FLUTTER_EXE):
        log(f'Flutter exe NOT FOUND at: {FLUTTER_EXE}')
        return None
    if _flutter_proc is not None and _flutter_proc.poll() is None:
        log('Flutter already running, skipping launch')
        return _flutter_proc
    try:
        log(f'Launching Flutter: {FLUTTER_EXE}')
        # Tell the UI which DB to read — packaged builds may store it next to
        # the exe (see db._data_dir()) rather than under ~/.dockie.
        env = dict(os.environ)
        env['DOCKIE_DB_PATH'] = db.DB_PATH
        log(f'Flutter DB path passed: {db.DB_PATH}')
        proc = subprocess.Popen(
            [FLUTTER_EXE],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        # Pipe Flutter output to our stdout in a background thread
        def _pipe_output():
            for line in proc.stdout:
                log(f'flutter: {line.rstrip()}')
        threading.Thread(target=_pipe_output, daemon=True).start()
        _flutter_proc = proc
        log(f'Flutter launched (pid={proc.pid})')
        return proc
    except Exception:
        log_exc(f'Failed to launch Flutter: {FLUTTER_EXE}')
        return None


# ── Startup registration ──
STARTUP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_NAME = 'Dockie'
# Older/installer names that also register the app for auto-start. The tray
# toggle and startup sync manage all of them so unticking actually removes
# every startup entry, not just the current name.
_STARTUP_ALIASES = ('Dockie', 'FileFinder', 'DockieLauncher')


def _startup_command():
    # Packaged (PyInstaller) builds run as a single exe — register that exe.
    if getattr(sys, 'frozen', False):
        return f'"{os.path.abspath(sys.executable)}"'
    # Running from source — register pythonw.exe + this script.
    pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.abspath(__file__)
    return f'"{pythonw}" "{script}"'


def _load_settings():
    try:
        with open(SETTINGS_PATH, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as e:
        log(f'Settings: failed to load {SETTINGS_PATH}: {e}', level='WARN')
        return {}


def _save_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SETTINGS_PATH, 'w') as f:
            json.dump(settings, f)
    except OSError as e:
        log(f'Settings: failed to save {SETTINGS_PATH}: {e}', level='ERROR')


def get_run_on_startup():
    return bool(_load_settings().get('run_on_startup', True))


def set_run_on_startup(enabled):
    settings = _load_settings()
    settings['run_on_startup'] = bool(enabled)
    _save_settings(settings)


def _delete_startup_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass  # already not registered
    except OSError as e:
        log(f'Failed to unregister startup ({name}): {e}')


def enable_run_on_startup():
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, _startup_command())
    except OSError as e:
        log(f'Failed to register startup: {e}')
    # Remove duplicate entries under older/alternate names so the app
    # auto-starts exactly once.
    for name in _STARTUP_ALIASES:
        if name != STARTUP_NAME:
            _delete_startup_value(name)


def disable_run_on_startup():
    for name in _STARTUP_ALIASES:
        _delete_startup_value(name)


def sync_run_on_startup():
    """Apply the persisted preference at startup (default: auto-register)."""
    if get_run_on_startup():
        enable_run_on_startup()
    else:
        disable_run_on_startup()


def _create_update_mutex():
    """Create the named mutex the Inno installer waits on (AppMutex=Dockie).

    With CloseApplications=yes in the .iss, a reinstall/update closes a
    running Dockie before replacing its files instead of hitting a lock.
    The HANDLE is intentionally kept for the process lifetime."""
    global _update_mutex
    try:
        import ctypes
        _update_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Dockie')
    except Exception:
        _update_mutex = None


_update_mutex = None


def _migrate_config():
    """Move app data into the current data dir on first run.

    Covers the rebrand move (.filefinder -> .dockie) and the move of
    packaged builds that previously stored the DB next to the exe (e.g. an
    elevated Program Files install). Older dirs are checked newest first so
    the most recent data wins.
    """
    if os.path.exists(os.path.join(CONFIG_DIR, 'index.db')):
        return  # already in place or fresh install
    old_dirs = [os.path.join(os.path.expanduser('~'), '.dockie'),
                os.path.join(os.path.expanduser('~'), '.filefinder')]
    if getattr(sys, 'frozen', False):
        old_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    for old_dir in old_dirs:
        if not os.path.isdir(old_dir) or os.path.abspath(old_dir) == os.path.abspath(CONFIG_DIR):
            continue
        os.makedirs(CONFIG_DIR, exist_ok=True)
        migrated = False
        for name in ('index.db', 'index.db-wal', 'index.db-shm', 'settings.json'):
            src = os.path.join(old_dir, name)
            dst = os.path.join(CONFIG_DIR, name)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    os.replace(src, dst)
                except OSError:
                    shutil.move(src, dst)  # cross-drive (install dir on another volume)
                log(f'Migrated {name} -> {CONFIG_DIR}')
                migrated = True
        if migrated:
            return


# ── System tray ──
_tray_icon = None  # global reference keeps the icon alive while running
_bridge = None  # Qt signal bridge for tray -> window (set once in main())


def _make_tray_icon():
    """Tray icon: robot.ico when available, else a generated fallback."""
    if getattr(sys, 'frozen', False):
        path = os.path.join(sys._MEIPASS, 'robot.ico')
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'robot.ico')
    if os.path.exists(path):
        try:
            return Image.open(path).convert('RGBA')
        except OSError:
            pass
    # Fallback: 64x64 file-finder icon (magnifying glass over a document).
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Document shape (light blue rounded rect)
    d.rounded_rectangle([8, 4, 48, 56], radius=4, fill=(100, 149, 237, 255))
    # Folded corner
    d.polygon([(40, 4), (48, 4), (48, 12), (40, 4)], fill=(70, 130, 200, 255))
    # Magnifying glass circle
    d.ellipse([24, 26, 44, 46], outline=(255, 255, 255, 255), width=3)
    # Handle
    d.line([(38, 40), (50, 52)], fill=(255, 255, 255, 255), width=3)
    return img


def _tray_show(icon, item):
    log('Tray: Show clicked')
    if _bridge is not None:
        _bridge.show_requested.emit()


def _tray_exit(icon, item):
    log('Tray: Exit clicked')
    _state.shutdown_flag.set()
    _state.cancel.set()
    if _bridge is not None:
        _bridge.quit_requested.emit()
    try:
        icon.stop()
    except Exception:
        log_exc('Tray: failed to stop icon')


def _toggle_run_on_startup(icon, item):
    try:
        enabled = not get_run_on_startup()
        set_run_on_startup(enabled)
        if enabled:
            enable_run_on_startup()
            log('Run on startup: enabled')
        else:
            disable_run_on_startup()
            log('Run on startup: disabled')
    except Exception:
        log_exc('Tray: failed to toggle run-on-startup')


def _run_tray():
    global _tray_icon
    try:
        log('Tray: creating icon...')
        _tray_icon = pystray.Icon(
            'dockie',
            _make_tray_icon(),
            'Dockie',
            menu=pystray.Menu(
                pystray.MenuItem('Show', _tray_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    'Run on startup',
                    _toggle_run_on_startup,
                    checked=lambda item: get_run_on_startup(),
                ),
                pystray.Menu.SEPARATOR,
                # Informational only: a None action renders the item disabled.
                pystray.MenuItem(f'Version {Updater.VERSION}', None),
                pystray.MenuItem('Exit', _tray_exit),
            ),
        )
        _tray_icon.run_detached()
        log('Tray: running detached')
    except Exception:
        log_exc('Tray: failed to start')
        _tray_icon = None


# ── Indexing status window (PyQt6) ──
class WindowBridge(QObject):
    show_requested = pyqtSignal()
    quit_requested = pyqtSignal()


class IndexingWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(300)
        self._refresh()

    def _init_ui(self):
        self.setWindowTitle('Dockie')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setFixedSize(420, 220)
        self.setStyleSheet('''
            QWidget { background-color: #1e1e2e; color: #cdd6f4;
                font-family: "Segoe UI", sans-serif; font-size: 12px; }
            QProgressBar { border: 1px solid #45475a; border-radius: 4px;
                background-color: #313244; text-align: center;
                color: #cdd6f4; height: 18px; }
            QProgressBar::chunk { background-color: #89b4fa; border-radius: 3px; }
            QPushButton { background-color: #313244; border: 1px solid #45475a;
                border-radius: 5px; padding: 4px 12px; color: #cdd6f4; }
            QPushButton:hover { background-color: #45475a; }
            QPushButton#btnCancel { background-color: #f38ba8; color: #1e1e2e; }
            QPushButton#btnCancel:hover { background-color: #f2cdcd; }
        ''')

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        self.lbl_title = QLabel('Dockie — Indexing…')
        self.lbl_title.setFont(QFont('Segoe UI', 13, QFont.Weight.Bold))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_title)

        self.lbl_stats = QLabel('')
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_stats)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.lbl_current = QLabel('')
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_current.setWordWrap(True)
        layout.addWidget(self.lbl_current)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        self.btn_hide = QPushButton('Hide')
        self.btn_hide.clicked.connect(lambda: self.hide())
        btn_layout.addWidget(self.btn_hide)

        self.btn_pause = QPushButton('Pause')
        self.btn_pause.clicked.connect(lambda: self._toggle_pause())
        btn_layout.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton('Cancel')
        self.btn_cancel.setObjectName('btnCancel')
        self.btn_cancel.clicked.connect(lambda: self._cancel())
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        self.setLayout(layout)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20,
                  screen.bottom() - self.height() - 20)

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _refresh(self):
        phase = _state.phase
        cancelled = _state.cancel.is_set() and phase != 'done'

        if cancelled:
            title = 'Dockie — Cancelled'
            in_progress = False
        elif phase == 'done':
            title = 'Dockie — Done'
            in_progress = False
        elif phase == 'scan':
            title = 'Dockie — Scanning…'
            in_progress = True
        elif phase == 'extract':
            title = 'Dockie — Indexing…'
            in_progress = True
        else:
            title = 'Dockie — Starting…'
            in_progress = True

        self.lbl_title.setText(title)

        pending = max(0, _state.files_found - _state.files_done)
        if phase == 'scan':
            self.lbl_stats.setText(f'Found: {_state.files_found:,}')
        else:
            self.lbl_stats.setText(
                f'Found: {_state.files_found:,}  |  Done: {_state.files_done:,}'
                f'  |  Pending: {pending:,}')

        if cancelled:
            self.lbl_current.setText('Indexing cancelled.')
        elif phase == 'done':
            self.lbl_current.setText('All files indexed.')
        else:
            self.lbl_current.setText(_state.current_file or '')

        if _state.files_found > 0:
            self.progress_bar.setRange(0, _state.files_found)
            self.progress_bar.setValue(min(_state.files_done, _state.files_found))
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)

        self.btn_pause.setText('Resume' if _state.pause.is_set() else 'Pause')
        self.btn_pause.setVisible(in_progress)
        self.btn_cancel.setVisible(in_progress)
        self.btn_hide.setVisible(True)

    def _toggle_pause(self):
        if _state.pause.is_set():
            _state.pause.clear()
        else:
            _state.pause.set()
        self._refresh()

    def _cancel(self):
        _state.cancel.set()
        self._refresh()


# ── Main ──
def main():
    global _bridge
    try:
        # If a newer release exists, download + relaunch the installer and
        # exit so the current executable releases its file lock.
        if Updater.check_and_update():
            return
        _migrate_config()
        _create_update_mutex()
        log('========================================')
        log('Dockie backend starting...')
        log(f'Config dir: {CONFIG_DIR}')
        log(f'DB path: {db.DB_PATH}')
        os.makedirs(CONFIG_DIR, exist_ok=True)
        sync_run_on_startup()
    except Exception:
        log_exc('Startup failed (config)')

    # Qt application (created first; its event loop runs on the main thread).
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
    except Exception:
        log_exc('Fatal: failed to create Qt application')
        return

    # Scan + extract in background
    log('Starting scan/extract pipeline...')
    pipeline_thread = threading.Thread(target=pipeline, daemon=True)
    pipeline_thread.start()

    # Hotkey: triple-Ctrl (left or right) via a pynput listener
    log('Starting hotkey listener (triple-Ctrl)...')
    global _hotkey_listener
    _hotkey_listener = keyboard.Listener(on_press=_on_press)
    _hotkey_listener.daemon = True
    try:
        _hotkey_listener.start()
    except Exception:
        log_exc('Hotkey listener failed to start')
    if _hotkey_listener.is_alive():
        log('Hotkey listener active (triple-Ctrl)')
    else:
        log('Hotkey listener NOT running - triple-Ctrl disabled', level='WARN')

    # Start the file watcher once the pipeline finishes
    def _after_pipeline():
        global _watcher
        try:
            pipeline_thread.join()
            if not _state.cancel.is_set():
                log('Pipeline done, starting file watcher...')
                _watcher = start_watcher()
                log('Ready — waiting for hotkey or shutdown')
        except Exception:
            log_exc('Pipeline/watcher thread failed')

    threading.Thread(target=_after_pipeline, daemon=True).start()

    log('========================================')

    # Indexing window (hidden until the tray "Show" item is clicked).
    try:
        bridge = WindowBridge()
        window = IndexingWindow()
        bridge.show_requested.connect(window.show_and_raise)
        bridge.quit_requested.connect(app.quit)
        _bridge = bridge
        log('Indexing window ready (hidden)')
    except Exception:
        log_exc('Indexing window failed to initialize')
        _bridge = None
        window = None

    # Start the tray (detached) so the main thread can run the Qt event loop.
    if _USE_TRAY:
        _run_tray()
        log('System tray active')
    elif window is not None:
        log('System tray unavailable — showing window directly')
        window.show_and_raise()

    try:
        app.exec()
    except Exception:
        log_exc('Qt event loop crashed')

    log('Shutting down...')
    _state.cancel.set()
    try:
        if _hotkey_listener is not None:
            _hotkey_listener.stop()
    except Exception:
        pass
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            pass
    if _flutter_proc:
        try:
            _flutter_proc.terminate()
            _flutter_proc.wait(timeout=5)
        except Exception:
            log_exc('Failed to terminate Flutter — killing')
            _flutter_proc.kill()

    log('Done.')


if __name__ == '__main__':
    main()
