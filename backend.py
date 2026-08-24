"""
Dockie backend - scanning, extraction, file watching and update checks.

Runs as a child of the Flutter main process (dockie_ui.exe), which owns the
triple-Ctrl hotkey, the tray icon and the search overlay. Communication is
line-based over stdio:

  stdin (commands from Flutter):
      PING | SHUTDOWN | GET_STATUS | GET_VERSION | GET_RUN_ON_STARTUP |
      RUN_ON_STARTUP <0|1>

  stdout (events to Flutter):
      READY | VERSION <v> | STATUS <phase> <found> <done> <current> |
      RUN_ON_STARTUP <0|1> | PONG | UPDATE_EXITING <v>

When stdin reaches EOF (the parent closed the pipe), or when the parent
process exits (DOCKIE_PARENT_PID watch), the backend shuts down, so a crashed
UI never leaves an orphan backend behind. Flutter relaunches us if we die, so
the index pipeline stays up as long as the app runs.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg

import Updater

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from scanner import find_pdfs, get_available_roots
from content_extractor import extract_text
import db
import applog
from applog import log, log_exc

# App data always lives in the per-user .dockie dir - never in the install
# folder (Program Files is read-only for non-elevated runs, and an elevated
# first run would otherwise split data across two locations). See db._data_dir().
CONFIG_DIR = db.DATA_DIR
SETTINGS_PATH = os.path.join(CONFIG_DIR, 'settings.json')
LOG_PATH = os.path.join(CONFIG_DIR, 'dockie.log')
applog.configure(LOG_PATH)

# When launched without a console (pythonw.exe, e.g. at login), redirect
# stray prints (third-party output, our own logs) into dockie.log so
# logging never fails on a missing stdout/stderr. applog.log() writes to
# the same handle directly.
if sys.stdout is None or sys.stderr is None:
    _log_handle = applog.get_handle()
    if sys.stdout is None:
        sys.stdout = _log_handle
    if sys.stderr is None:
        sys.stderr = _log_handle
else:
    # Piped stdio is the IPC channel - force UTF-8 so filenames with non-ASCII
    # characters survive the trip to the Flutter side.
    for _stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            pass


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# File watcher
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Scan + Extract pipeline
# ---------------------------------------------------------------------------
def run_scan():
    log('Scan started - walking filesystem for PDFs...')
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

        # Start the persistent extractor - it drains the initial backlog now and
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


# ---------------------------------------------------------------------------
# Startup registration
# ---------------------------------------------------------------------------
STARTUP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_NAME = 'Dockie'
# Older/installer names that also register the app for auto-start. The tray
# toggle and startup sync manage all of them so unticking actually removes
# every startup entry, not just the current name.
_STARTUP_ALIASES = ('Dockie', 'FileFinder', 'DockieLauncher')


def _startup_command():
    """The main process (Flutter dockie_ui.exe) is the app entry point, so
    the Run key launches that executable. The backend (this process) is
    started by the UI, never directly by Windows."""
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        ui = os.path.join(exe_dir, 'dockie_ui.exe')
        if os.path.exists(ui):
            return f'"{ui}"'
        return f'"{os.path.abspath(sys.executable)}"'  # backend run standalone
    # Running from source - register the Flutter dev build when present.
    ui = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'dockie_ui', 'build', 'windows', 'x64',
                      'runner', 'Debug', 'dockie_ui.exe')
    if not os.path.exists(ui):
        ui = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'dockie_ui', 'build', 'windows', 'x64',
                          'runner', 'Release', 'dockie_ui.exe')
    if os.path.exists(ui):
        return f'"{ui}"'
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

    The Flutter main process creates it too; both processes hold it so a
    reinstall/update closes the whole app before replacing files. The HANDLE
    is intentionally kept for the process lifetime."""
    global _update_mutex
    try:
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


# ---------------------------------------------------------------------------
# IPC with the Flutter main process
# ---------------------------------------------------------------------------
def _emit(line):
    """Send one protocol line to the Flutter process. Never raises."""
    try:
        print(line, flush=True)
    except Exception:
        pass


def _status_line():
    with _state.lock:
        return (f'STATUS {_state.phase} {_state.files_found} '
                f'{_state.files_done} {_state.current_file}')


def _emit_status():
    _emit(_status_line())


def _status_loop():
    """Periodically push the current pipeline state so the overlay can show
    live indexing progress without any pull handshake."""
    while not _state.shutdown_flag.wait(timeout=1.0):
        _emit_status()


def _handle_command(command):
    if command == 'PING':
        _emit('PONG')
    elif command == 'GET_STATUS':
        _emit_status()
    elif command == 'GET_VERSION':
        _emit(f'VERSION {Updater.VERSION}')
    elif command == 'GET_RUN_ON_STARTUP':
        _emit(f'RUN_ON_STARTUP {1 if get_run_on_startup() else 0}')
    elif command.startswith('RUN_ON_STARTUP '):
        enabled = command.split()[1] == '1'
        set_run_on_startup(enabled)
        if enabled:
            enable_run_on_startup()
        else:
            disable_run_on_startup()
        log(f'Run on startup: {"enabled" if enabled else "disabled"}')
        _emit(f'RUN_ON_STARTUP {1 if enabled else 0}')
    else:
        log(f'Unknown command: {command!r}', level='WARN')


def _run_command_loop():
    """Block reading commands from the Flutter process. stdin reaching EOF
    (parent closed the pipe, i.e. the UI is gone) triggers a clean shutdown."""
    if sys.stdin is None:
        # No pipe (standalone run) - stay up until the parent watch or a
        # manual shutdown fires.
        _state.shutdown_flag.wait()
        return
    for line in sys.stdin:
        if _state.shutdown_flag.is_set():
            break
        command = line.strip()
        if not command:
            continue
        if command == 'SHUTDOWN':
            break
        try:
            _handle_command(command)
        except Exception:
            log_exc(f'Command handler failed: {command!r}')
    _shutdown()


# ---------------------------------------------------------------------------
# Parent-death detection (backstop for stdin EOF)
# ---------------------------------------------------------------------------
def _parent_alive(parent_pid):
    """True while the process with `parent_pid` is still running."""
    try:
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
            False, parent_pid)
        if not handle:
            # ERROR_ACCESS_DENIED means the process exists but is out of
            # reach - treat it as alive. Anything else means it is gone.
            return ctypes.get_last_error() == 5
        try:
            # Signaled handle == process exited; WAIT_TIMEOUT == still alive.
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return True  # assume alive on any error


def _watch_parent(parent_pid):
    while not _state.shutdown_flag.wait(timeout=3.0):
        if not _parent_alive(parent_pid):
            log(f'Parent (pid {parent_pid}) exited - shutting down backend')
            _state.shutdown_flag.set()
            _state.cancel.set()
            break


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------
def _shutdown():
    if _state.shutdown_flag.is_set():
        return
    _state.shutdown_flag.set()
    _state.cancel.set()
    log('Shutting down...')
    global _watcher
    if _watcher is not None:
        try:
            _watcher.stop()
        except Exception:
            log_exc('Watcher stop failed')
    _state.extract_wake.set()
    log('Done.')


def _notify_update(version):
    """Tell the Flutter main process an update is about to install, and give
    it a moment to exit (releasing file locks and the AppMutex) before the
    installer runs."""
    _emit(f'UPDATE_EXITING {version}')
    time.sleep(2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    try:
        # If a newer release exists, notify the UI, launch the installer and
        # exit so the current executables release their file locks.
        if Updater.check_and_update(on_update=_notify_update):
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

    # Scan + extract in background
    log('Starting scan/extract pipeline...')
    pipeline_thread = threading.Thread(target=pipeline, daemon=True)
    pipeline_thread.start()

    # Start the file watcher once the pipeline finishes
    def _after_pipeline():
        global _watcher
        try:
            pipeline_thread.join()
            if not _state.cancel.is_set():
                log('Pipeline done, starting file watcher...')
                _watcher = start_watcher()
                log('Ready - waiting for commands or shutdown')
        except Exception:
            log_exc('Pipeline/watcher thread failed')

    threading.Thread(target=_after_pipeline, daemon=True).start()

    # Periodic status push so the UI always has fresh indexing progress.
    threading.Thread(target=_status_loop, daemon=True).start()

    # Parent watch: exit when the Flutter process is gone.
    parent_pid = os.environ.get('DOCKIE_PARENT_PID')
    if parent_pid and parent_pid.isdigit():
        log(f'Watching parent pid {parent_pid}')
        threading.Thread(target=_watch_parent,
                         args=(int(parent_pid),), daemon=True).start()

    log('========================================')

    # Announce readiness and current state; the UI uses these to sync the
    # tray (version, run-on-startup) and the overlay status.
    _emit(f'VERSION {Updater.VERSION}')
    _emit(f'RUN_ON_STARTUP {1 if get_run_on_startup() else 0}')
    _emit('READY')
    _emit_status()

    _run_command_loop()


if __name__ == '__main__':
    main()
