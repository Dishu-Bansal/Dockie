"""
Indexer GUI — scans system for PDFs, extracts text, stores in SQLite.
Shows a bottom-right popup with progress, system tray, pause/cancel/hide.
"""

import os
import sys
import time
import sqlite3
import queue
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer,
)
from PyQt6.QtGui import QIcon, QFont, QAction

from content_extractor import extract_text


# ── Paths ──
DB_DIR = os.path.join(os.path.expanduser('~'), '.filefinder')
DB_PATH = os.path.join(DB_DIR, 'index.db')

# ── Exclusion lists (shared with pdf_scanner.py) ──
SYSTEM_ROOT_NAMES = {
    '$Recycle.Bin', 'System Volume Information', '$WINDOWS.~TMP',
    '$Windows.~WS', '$WinREAgent', 'Recovery', 'MSOCache',
    'Config.Msi', 'PerfLogs', 'boot', 'EFI',
}

SKIP_PATH_PREFIXES_C = [
    r'C:\Windows', r'C:\Windows.old', r'C:\WinNT',
    r'C:\Program Files', r'C:\Program Files (x86)',
    r'C:\ProgramData', r'C:\Documents and Settings',
]

PRUNED_DIR_NAMES = {
    'node_modules', '.venv', 'venv', '.env', 'vendor',
    'bower_components', '.yarn', '.pnpm-store',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.tox',
    '.nox', 'dist', 'build', 'eggs', '.eggs',
    '.git', '.svn', '.hg',
    '.npm', '.cargo', '.gradle', '.m2', '.ivy2', '.sbt',
    '.nuget', '.rustup',
    'target', 'obj', 'bin', 'Debug', 'Release', 'x64', 'x86',
    'Generated', 'out', '.next', '.nuxt',
    '.cache', 'cache', '.thumbnails', 'thumbnails',
    'tmp', 'temp', 'logs', '.log',
    'Sdk', 'WUDownloadCache', 'vcpkg', 'Anaconda',
}

SKIP_USER_SUBDIRS = [
    r'AppData\Local\Temp', r'AppData\Local\Microsoft',
    r'AppData\Local\Packages', r'AppData\Local\Programs',
    r'AppData\Local\MicrosoftEdge', r'AppData\Local\Google',
    r'AppData\Local\Mozilla', r'AppData\Local\pip',
    r'AppData\Local\pnpm', r'AppData\Local\Yarn',
    r'AppData\Local\NuGet', r'AppData\Local\Docker',
    r'AppData\Local\JetBrains', r'AppData\Local\cache',
    r'AppData\Roaming\npm', r'AppData\Roaming\Code',
    r'AppData\Roaming\JetBrains', r'AppData\Roaming\Docker',
    r'AppData\Roaming\Composer', r'AppData\Roaming\NuGet',
    r'AppData\LocalLow',
    r'.nuget', r'.m2', r'.gradle', r'.cargo', r'.rustup', r'.yarn',
]


# ── Database ──
def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute('''CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        filename TEXT,
        text TEXT,
        size INTEGER,
        modified REAL,
        indexed_at REAL
    )''')
    conn.commit()
    return conn


def store_file(conn, path, text):
    filename = os.path.basename(path)
    try:
        stat = os.stat(path)
        size = stat.st_size
        modified = stat.st_mtime
    except OSError:
        size = 0
        modified = 0
    now = time.time()
    conn.execute(
        'INSERT OR REPLACE INTO files (path, filename, text, size, modified, indexed_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (path, filename, text, size, modified, now)
    )
    conn.commit()


# ── Helpers ──
def get_available_roots():
    roots = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        root = f'{letter}:\\'
        if os.path.exists(root):
            roots.append(root)
    return roots


def build_user_skip_set():
    skip_set = set()
    users_base = r'C:\Users'
    if os.path.exists(users_base):
        try:
            for entry in os.scandir(users_base):
                if entry.is_dir():
                    for sub in SKIP_USER_SUBDIRS:
                        sp = os.path.normpath(os.path.join(entry.path, sub))
                        skip_set.add(sp)
        except PermissionError:
            pass
    return skip_set


def should_skip_root(dirpath, drive):
    dp = os.path.normpath(dirpath)
    if drive == 'C:':
        for prefix in SKIP_PATH_PREFIXES_C:
            pn = os.path.normpath(prefix)
            if dp == pn or dp.startswith(pn + os.sep):
                return True
    drive_norm = os.path.normpath(drive + '\\')
    parent = os.path.dirname(dp)
    if parent == drive_norm or parent == drive_norm.rstrip(os.sep):
        if os.path.basename(dp) in SYSTEM_ROOT_NAMES:
            return True
    return False


# ── Workers ──
class ScanWorker(QThread):
    """Walks the filesystem, finds PDFs, pushes paths to a queue."""
    progress = pyqtSignal(int, int, str)   # dirs_visited, pdfs_found, current_dir
    finished = pyqtSignal(int)              # total pdfs found

    def __init__(self, path_queue, cancel_event, scanning_event):
        super().__init__()
        self.path_queue = path_queue
        self._cancel = cancel_event
        self._scanning = scanning_event  # cleared when scan is truly done

    def run(self):
        try:
            self._scanning.set()
            roots = get_available_roots()
            user_skip_set = build_user_skip_set()
            total_found = 0
            last_progress = 0

            for root in roots:
                if self._cancel.is_set():
                    break
                drive = root.rstrip('\\/')
                for dirpath, dirnames, filenames in self._walk(root):
                    if self._cancel.is_set():
                        break
                    if should_skip_root(dirpath, drive):
                        dirnames.clear()
                        continue
                    dirnames[:] = [d for d in dirnames if d not in PRUNED_DIR_NAMES]
                    dpn = os.path.normpath(dirpath)
                    skip = False
                    for sp in user_skip_set:
                        if dpn == sp or dpn.startswith(sp + os.sep):
                            dirnames.clear()
                            skip = True
                            break
                    if skip:
                        continue
                    for fname in filenames:
                        if fname.lower().endswith('.pdf'):
                            full = os.path.join(dirpath, fname)
                            total_found += 1
                            self.path_queue.put(full)
                    # Throttle progress to every 3 seconds
                    now = time.time()
                    if now - last_progress >= 3:
                        self.progress.emit(0, total_found, dirpath)
                        last_progress = now

            self.finished.emit(total_found)
        finally:
            self._scanning.clear()

    def _walk(self, root):
        """os.walk wrapper that catches PermissionError and moves on."""
        try:
            yield from os.walk(root, followlinks=False)
        except PermissionError:
            pass


class ExtractWorker(QThread):
    """Pops paths from queue, extracts text, stores in DB."""
    file_done = pyqtSignal(str, bool)       # path, has_text
    all_done = pyqtSignal()

    def __init__(self, path_queue, db_conn, pause_event, cancel_event, scanning_event):
        super().__init__()
        self.path_queue = path_queue
        self.db_conn = db_conn
        self._pause = pause_event
        self._cancel = cancel_event
        self._scanning = scanning_event

    def run(self):
        try:
            while not self._cancel.is_set():
                if self._pause.is_set():
                    self.msleep(200)
                    continue

                try:
                    path = self.path_queue.get(timeout=0.5)
                except queue.Empty:
                    # Only exit if scanner is done AND queue is truly empty
                    if not self._scanning.is_set():
                        break
                    continue

                text = extract_text(path)
                has_text = bool(text)
                try:
                    store_file(self.db_conn, path, text)
                    self.file_done.emit(path, has_text)
                except Exception:
                    self.file_done.emit(path, False)
        finally:
            self.all_done.emit()


# ── GUI ──
class IndexerWindow(QWidget):
    """Bottom-right popup with progress, hide/pause/cancel buttons."""

    def __init__(self):
        super().__init__()
        self.path_queue = queue.Queue()
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()

        self.files_found = 0
        self.files_indexed = 0
        self.files_empty = 0
        self.scan_done = False
        self.extract_done = False
        self.running = True

        self.db_conn = None  # set after init_db

        self._init_ui()
        self._init_tray()
        self._init_workers()

    # ── UI setup ──
    def _init_ui(self):
        self.setWindowTitle('FileFinder — Indexing')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setFixedSize(420, 220)
        self.setStyleSheet('''
            QWidget {
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: "Segoe UI", sans-serif;
                font-size: 12px;
                border: 1px solid #45475a;
                border-radius: 10px;
            }
            QProgressBar {
                border: 1px solid #45475a;
                border-radius: 4px;
                background-color: #313244;
                text-align: center;
                color: #cdd6f4;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #89b4fa;
                border-radius: 3px;
            }
            QPushButton {
                background-color: #313244;
                border: 1px solid #45475a;
                border-radius: 5px;
                padding: 4px 12px;
                color: #cdd6f4;
            }
            QPushButton:hover {
                background-color: #45475a;
            }
            QPushButton#btnCancel {
                background-color: #f38ba8;
                color: #1e1e2e;
            }
            QPushButton#btnCancel:hover {
                background-color: #f2cdcd;
            }
        ''')

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # Title
        title = QLabel('FileFinder — Indexing PDFs')
        title.setFont(QFont('Segoe UI', 13, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Stats row
        self.lbl_stats = QLabel('Found: 0  |  Indexed: 0  |  Pending: 0')
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_stats)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Current file
        self.lbl_current = QLabel('Starting scan...')
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_current.setWordWrap(True)
        layout.addWidget(self.lbl_current)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_hide = QPushButton('Hide')
        self.btn_hide.clicked.connect(self._hide_to_tray)
        btn_layout.addWidget(self.btn_hide)

        self.btn_pause = QPushButton('Pause')
        self.btn_pause.clicked.connect(self._toggle_pause)
        btn_layout.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton('Cancel')
        self.btn_cancel.setObjectName('btnCancel')
        self.btn_cancel.clicked.connect(self._cancel)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        # Position at bottom-right
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20,
                   screen.bottom() - self.height() - 20)

    # ── System tray ──
    def _init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip('FileFinder — Indexing PDFs')

        # Use a simple colored pixmap as icon
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor('#89b4fa'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        painter.setPen(QColor('#1e1e2e'))
        font = QFont('Segoe UI', 14, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, 'F')
        painter.end()
        self.tray.setIcon(QIcon(pix))

        menu = QMenu()
        action_show = QAction('Show', menu)
        action_show.triggered.connect(self._show_from_tray)
        menu.addAction(action_show)
        menu.addSeparator()
        action_exit = QAction('Exit', menu)
        action_exit.triggered.connect(self._cancel)
        menu.addAction(action_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # ── Workers ──
    def _init_workers(self):
        self.db_conn = init_db()
        self._scanning_event = threading.Event()

        self.scanner = ScanWorker(
            self.path_queue, self._cancel_event, self._scanning_event
        )
        self.scanner.progress.connect(self._on_scan_progress)
        self.scanner.finished.connect(self._on_scan_finished)

        self.extractor = ExtractWorker(
            self.path_queue, self.db_conn,
            self._pause_event, self._cancel_event, self._scanning_event
        )
        self.extractor.file_done.connect(self._on_file_done)
        self.extractor.all_done.connect(self._on_all_done)

        self.scanner.start()
        self.extractor.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start(500)

    # ── Slots ──
    def _on_scan_progress(self, dirs, pdfs, current):
        self.files_found = pdfs
        self.lbl_current.setText(f'Scanning: {current}')

    def _on_scan_finished(self, total):
        self.scan_done = True
        self.lbl_current.setText(f'Scan complete. {total} files found. Indexing...')

    def _on_file_done(self, path, has_text):
        self.files_indexed += 1
        if has_text:
            self.lbl_current.setText(f'Indexed: {os.path.basename(path)}')
        else:
            self.files_empty += 1

    def _on_all_done(self):
        self.extract_done = True
        self._refresh_stats()
        if not self._cancel_event.is_set():
            self.lbl_current.setText('All files indexed!')
            self.lbl_stats.setText(
                f'Found: {self.files_found}  |  Indexed: {self.files_indexed}'
                f'  |  Empty: {self.files_empty}'
            )
            self.btn_pause.setEnabled(False)
            self.btn_cancel.setText('Close')
            try:
                self.btn_cancel.clicked.disconnect()
            except Exception:
                pass
            self.btn_cancel.clicked.connect(self.close)

    def _refresh_stats(self):
        pending = max(0, self.files_found - self.files_indexed)
        self.lbl_stats.setText(
            f'Found: {self.files_found}  |  Indexed: {self.files_indexed}  |  Pending: {pending}'
        )
        if self.files_found > 0:
            self.progress_bar.setRange(0, self.files_found)
            self.progress_bar.setValue(self.files_indexed)
        self.tray.setToolTip(
            f'FileFinder\nFound: {self.files_found}\nIndexed: {self.files_indexed}\nPending: {pending}'
        )

    # ── Button actions ──
    def _hide_to_tray(self):
        self.hide()

    def _show_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.btn_pause.setText('Pause')
        else:
            self._pause_event.set()
            self.btn_pause.setText('Resume')

    def _cancel(self):
        self._cancel_event.set()
        self.scanner.wait(3000)
        self.extractor.wait(3000)
        self._timer.stop()
        if self.db_conn:
            self.db_conn.close()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if not self.extract_done:
            # Still indexing — hide to tray instead of quitting
            self._hide_to_tray()
            event.ignore()
        else:
            self._cancel()


# ── Entry ──
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = IndexerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
