"""
Dockie backend — scanning, extraction, file watching, hotkey, system tray,
and PyQt indexing-status window. Launches the Flutter UI on triple-F.
"""

import json
import os
import subprocess
import sys
import threading
import time
import winreg

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pynput import keyboard

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from scanner import find_pdfs, get_available_roots
from content_extractor import extract_text
import db

# System tray (optional — graceful if pystray/Pillow not installed)
_USE_TRAY = False
try:
    import pystray
    from PIL import Image, ImageDraw
    _USE_TRAY = True
except ImportError:
    pass

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.dockie')
SETTINGS_PATH = os.path.join(CONFIG_DIR, 'settings.json')

if getattr(sys, 'frozen', False):
    # Installed layout: Inno Setup installs dockie_ui.exe next to Dockie.exe.
    FLUTTER_EXE = os.path.join(os.path.dirname(sys.executable), 'dockie_ui.exe')
else:
    # Source layout (dev): dockie_ui\build\windows\x64\runner\Release\dockie_ui.exe
    FLUTTER_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'dockie_ui', 'build', 'windows', 'x64',
                               'runner', 'Release', 'dockie_ui.exe')

# When launched without a console (pythonw.exe, e.g. at login), redirect prints
# to a log file so logging does not fail on a missing stdout/stderr.
if sys.stdout is None or sys.stderr is None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    _log = open(os.path.join(CONFIG_DIR, 'dockie.log'), 'a', buffering=1,
                encoding='utf-8', errors='replace')
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
        finally:
            c.close()


# ── Scan + Extract pipeline ──
def run_scan():
    print('[backend] Scan started — walking filesystem for PDFs...')
    conn = db.get_conn()
    try:
        existing = db.get_all_paths(conn)
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
                print(f'[backend] Scan progress: {count:,} files found')
            if _state.cancel.is_set():
                conn.commit()
                print('[backend] Scan cancelled')
                return
        conn.commit()
        with _state.lock:
            _state.files_found = count
        print(f'[backend] Scan complete: {count:,} total files found')
    finally:
        conn.close()


def diff_scan():
    """Reconcile the DB with disk for changes made while the app was shutdown."""
    print('[backend] Diff scan started...')
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
                print('[backend] Diff scan cancelled')
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
        print(f'[backend] Diff scan complete: {new_count} new, '
              f'{modified_count} modified, {deleted_count} deleted')
    finally:
        conn.close()


def run_extract():
    print('[backend] Extraction worker started...')
    conn = db.get_conn()
    try:
        while not _state.cancel.is_set():
            if _state.pause.is_set():
                time.sleep(0.2)
                continue
            rows = db.get_pending_batch(conn, limit=1)
            if not rows:
                total = db.get_total_count(conn)
                indexed = db.get_indexed_count(conn)
                with _state.lock:
                    _state.files_found = total
                    _state.files_done = indexed
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
            try:
                text = extract_text(path)
            except Exception:
                text = ''
            db.mark_extracted(conn, path, text)
            conn.commit()
            with _state.lock:
                _state.files_done += 1
                if not text:
                    _state.files_empty += 1
            if _state.files_done % 50 == 0:
                print(f'[backend] Extract progress: {_state.files_done:,}/{_state.files_found:,}')
    finally:
        conn.close()
    print(f'[backend] Extraction worker stopped: {_state.files_done:,} indexed '
          f'({_state.files_empty:,} empty)')


def pipeline():
    conn = db.get_conn()
    try:
        db.init_db(conn)
        total = db.get_total_count(conn)
        pending = db.get_pending_count(conn)
    finally:
        conn.close()

    print(f'[backend] DB state: {total} total, {pending} pending extraction')

    if total == 0:
        _state.phase = 'scan'
        print('[backend] Phase -> scan (fresh start)')
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
            print(f'[backend] Resuming: {pending} pending files to index')

    # Start the persistent extractor — it drains the initial backlog now and
    # keeps running to index new/requeued files the watcher adds later.
    _state.phase = 'extract'
    print('[backend] Phase -> extract (persistent worker)')
    threading.Thread(target=run_extract, daemon=True).start()

    # Reconcile with disk in the background for changes made while shutdown.
    if total > 0:
        print('[backend] Starting background diff scan...')
        threading.Thread(target=diff_scan, daemon=True).start()


def start_watcher():
    try:
        roots = []
        for r in get_available_roots():
            if os.path.exists(r):
                roots.append(r)
        if not roots:
            print('[backend] Watcher: no drive roots found')
            return None
        print(f'[backend] Watcher: starting on {len(roots)} drives: {", ".join(roots)}')
        observer = Observer()
        handler = PdfWatcher()
        for r in roots:
            observer.schedule(handler, r, recursive=True)
        observer.start()
        print('[backend] Watcher: active')
        return observer
    except Exception as e:
        print(f'[backend] Watcher: failed to start — {e}')
        return None


# ── Hotkey listener ──
_last_f_times: list[float] = []


def _on_press(key):
    global _last_f_times
    try:
        is_f = (hasattr(key, 'char') and key.char and key.char.lower() == 'f')
    except Exception:
        try:
            is_f = (key == keyboard.Key.f)
        except Exception:
            return
    if not is_f:
        _last_f_times.clear()  # any other key resets the triple-F sequence
        return

    now = time.time()
    _last_f_times.append(now)
    _last_f_times = [t for t in _last_f_times if t > now - 1.0]

    if len(_last_f_times) >= 3:
        _last_f_times.clear()
        print('[backend] Hotkey: triple-F pressed, launching Flutter UI')
        launch_flutter()


# ── Flutter process management ──
_flutter_proc = None  # process handle for the on-demand Flutter UI


def launch_flutter():
    global _flutter_proc
    if not os.path.exists(FLUTTER_EXE):
        print(f'[backend] Flutter exe NOT FOUND at: {FLUTTER_EXE}')
        return None
    if _flutter_proc is not None and _flutter_proc.poll() is None:
        print('[backend] Flutter already running, skipping launch')
        return _flutter_proc
    try:
        print(f'[backend] Launching Flutter: {FLUTTER_EXE}')
        proc = subprocess.Popen(
            [FLUTTER_EXE],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Pipe Flutter output to our stdout in a background thread
        def _pipe_output():
            for line in proc.stdout:
                print(f'[flutter] {line.rstrip()}')
        threading.Thread(target=_pipe_output, daemon=True).start()
        _flutter_proc = proc
        print(f'[backend] Flutter launched (pid={proc.pid})')
        return proc
    except Exception as e:
        print(f'[backend] Failed to launch Flutter: {e}')
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
    except (FileNotFoundError, ValueError):
        return {}


def _save_settings(settings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_PATH, 'w') as f:
        json.dump(settings, f)


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
        print(f'[backend] Failed to unregister startup ({name}): {e}')


def enable_run_on_startup():
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, _startup_command())
    except OSError as e:
        print(f'[backend] Failed to register startup: {e}')
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


def _migrate_config():
    """Move data from the old .filefinder dir into .dockie on first run."""
    old_dir = os.path.join(os.path.expanduser('~'), '.filefinder')
    if not os.path.isdir(old_dir) or old_dir == CONFIG_DIR:
        return
    if os.path.exists(os.path.join(CONFIG_DIR, 'index.db')):
        return  # already migrated or fresh install
    os.makedirs(CONFIG_DIR, exist_ok=True)
    for name in ('index.db', 'index.db-wal', 'index.db-shm', 'settings.json'):
        src = os.path.join(old_dir, name)
        dst = os.path.join(CONFIG_DIR, name)
        if os.path.exists(src) and not os.path.exists(dst):
            os.replace(src, dst)
            print(f'[backend] Migrated {name} -> {CONFIG_DIR}')


# ── System tray ──
_tray_icon = None  # global reference keeps the icon alive while running
_bridge = None  # Qt signal bridge for tray -> window (set once in main())


def _make_tray_icon():
    """Generate a 64x64 file-finder icon (magnifying glass over document)."""
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
    if _bridge is not None:
        _bridge.show_requested.emit()


def _tray_exit(icon, item):
    _state.shutdown_flag.set()
    _state.cancel.set()
    if _bridge is not None:
        _bridge.quit_requested.emit()
    icon.stop()


def _toggle_run_on_startup(icon, item):
    enabled = not get_run_on_startup()
    set_run_on_startup(enabled)
    if enabled:
        enable_run_on_startup()
        print('[backend] Run on startup: enabled')
    else:
        disable_run_on_startup()
        print('[backend] Run on startup: disabled')


def _run_tray():
    global _tray_icon
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
            pystray.MenuItem('Exit', _tray_exit),
        ),
    )
    _tray_icon.run_detached()


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
            self.progress_bar.setValue(_state.files_done)
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
    _migrate_config()
    print('[backend] ========================================')
    print('[backend] Dockie backend starting...')
    print(f'[backend] Config dir: {CONFIG_DIR}')
    print(f'[backend] DB path: {db.DB_PATH}')
    os.makedirs(CONFIG_DIR, exist_ok=True)
    sync_run_on_startup()

    # Qt application (created first; its event loop runs on the main thread).
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Scan + extract in background
    print('[backend] Starting scan/extract pipeline...')
    pipeline_thread = threading.Thread(target=pipeline, daemon=True)
    pipeline_thread.start()

    # Hotkey listener
    print('[backend] Starting hotkey listener...')
    hotkey_listener = keyboard.Listener(on_press=_on_press)
    hotkey_listener.daemon = True
    hotkey_listener.start()
    print('[backend] Hotkey listener active (triple-tap F)')

    # Start the file watcher once the pipeline finishes
    def _after_pipeline():
        global _watcher
        pipeline_thread.join()
        if not _state.cancel.is_set():
            print('[backend] Pipeline done, starting file watcher...')
            _watcher = start_watcher()
            print('[backend] Ready — waiting for hotkey or shutdown')

    threading.Thread(target=_after_pipeline, daemon=True).start()

    print('[backend] ========================================')

    # Indexing window (hidden until the tray "Show" item is clicked).
    bridge = WindowBridge()
    window = IndexingWindow()
    bridge.show_requested.connect(window.show_and_raise)
    bridge.quit_requested.connect(app.quit)
    _bridge = bridge
    print('[backend] Indexing window ready (hidden)')

    # Start the tray (detached) so the main thread can run the Qt event loop.
    if _USE_TRAY:
        _run_tray()
        print('[backend] System tray active')
    else:
        print('[backend] System tray unavailable — showing window directly')
        window.show_and_raise()

    app.exec()

    print('[backend] Shutting down...')
    _state.cancel.set()
    try:
        hotkey_listener.stop()
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
            _flutter_proc.kill()

    print('[backend] Done.')


if __name__ == '__main__':
    main()
