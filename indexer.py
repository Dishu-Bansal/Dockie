"""
Indexer GUI — two-phase: scan (populate DB) then extract (from DB).
File watcher keeps DB in sync. Crash-proof: resumes extraction on restart.
Bottom-right popup, system tray, pause/cancel/hide.
"""

import os
import sys
import time
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QFont, QAction

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from scanner import find_pdfs
from content_extractor import extract_text
from search_ui import SearchBar, SearchBridge, start_listener
import db


# ── File Watcher ──
class PdfWatcher(FileSystemEventHandler):
    def __init__(self):
        pass

    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        self._with_conn(lambda c: db.insert_scan_result(c, event.src_path))

    def on_modified(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        self._with_conn(lambda c: db.mark_extracted(c, event.src_path, None))

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


# ── Workers ──
class ScanWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(int)

    def __init__(self, cancel_event, is_diff=False):
        super().__init__()
        self._cancel = cancel_event
        self._is_diff = is_diff

    def run(self):
        conn = db.get_conn()
        try:
            total = 0
            if self._is_diff:
                existing = db.get_all_paths(conn)
                for path in find_pdfs(self._cancel):
                    total += 1
                    if path not in existing:
                        db.insert_scan_result(conn, path)
                        if total % 200 == 0:
                            conn.commit()
                    if total % 500 == 0:
                        self.progress.emit(total)
                conn.commit()
            else:
                for path in find_pdfs(self._cancel):
                    total += 1
                    db.insert_scan_result(conn, path)
                    if total % 200 == 0:
                        conn.commit()
                        self.progress.emit(total)
                conn.commit()

            self.progress.emit(total)
            self.finished.emit(total)
        finally:
            conn.close()


class ExtractWorker(QThread):
    file_done = pyqtSignal(str, bool)
    all_done = pyqtSignal()

    def __init__(self, pause_event, cancel_event):
        super().__init__()
        self._pause = pause_event
        self._cancel = cancel_event

    def run(self):
        conn = db.get_conn()
        try:
            while not self._cancel.is_set():
                if self._pause.is_set():
                    self.msleep(200)
                    continue
                rows = db.get_pending_batch(conn, limit=1)
                if not rows:
                    self.msleep(1000)
                    continue
                path, filename = rows[0]
                try:
                    text = extract_text(path)
                except Exception:
                    text = ""
                db.mark_extracted(conn, path, text)
                conn.commit()
                self.file_done.emit(filename, bool(text))
        finally:
            self.all_done.emit()
            conn.close()


# ── GUI ──
class IndexerWindow(QWidget):

    def __init__(self):
        super().__init__()
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self.files_found = 0
        self.files_done = 0
        self.files_empty = 0
        self.phase = 'scan'
        self.db_conn = db.get_conn()
        db.init_db(self.db_conn)
        self._init_ui()
        self._init_tray()
        self._init_search()
        self._start()

    def _init_ui(self):
        self.setWindowTitle('FileFinder')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setFixedSize(420, 220)
        self.setStyleSheet('''
            QWidget { background-color: #1e1e2e; color: #cdd6f4;
                font-family: "Segoe UI", sans-serif; font-size: 12px;
                border: 1px solid #45475a; border-radius: 10px; }
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

        self.lbl_title = QLabel('FileFinder — Scanning...')
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

        self.lbl_current = QLabel('Starting scan...')
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_current.setWordWrap(True)
        layout.addWidget(self.lbl_current)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_hide = QPushButton('Hide')
        self.btn_hide.clicked.connect(self._hide_to_tray)
        btn_layout.addWidget(self.btn_hide)

        self.btn_pause = QPushButton('Pause')
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_pause.setEnabled(False)
        btn_layout.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton('Cancel')
        self.btn_cancel.setObjectName('btnCancel')
        self.btn_cancel.clicked.connect(self._cancel)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20,
                   screen.bottom() - self.height() - 20)

    def _init_tray(self):
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip('FileFinder')
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor('#89b4fa'))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.setPen(QColor('#1e1e2e'))
        p.setFont(QFont('Segoe UI', 14, QFont.Weight.Bold))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, 'F')
        p.end()
        self.tray.setIcon(QIcon(pix))
        menu = QMenu()
        a = QAction('Show', menu)
        a.triggered.connect(self._show_from_tray)
        menu.addAction(a)
        menu.addSeparator()
        a2 = QAction('Exit', menu)
        a2.triggered.connect(self._cancel)
        menu.addAction(a2)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _init_search(self):
        self._search_bar = SearchBar()
        self._search_bridge = SearchBridge()
        self._search_bridge.show_signal.connect(self._search_bar.show_and_focus)
        self._hotkey_listener = start_listener(self._search_bridge)

    def _start(self):
        total = db.get_total_count(self.db_conn)
        pending = db.get_pending_count(self.db_conn)
        if total == 0:
            self._start_scan(is_diff=False)
        elif pending > 0:
            self._start_scan(is_diff=True)
        else:
            self._on_all_done()

    def _start_scan(self, is_diff=False):
        self.phase = 'scan'
        self.scanner = ScanWorker(self._cancel_event, is_diff=is_diff)
        self.scanner.progress.connect(self._on_scan_progress)
        self.scanner.finished.connect(self._on_scan_finished)
        self.scanner.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    def _start_extraction(self):
        self.phase = 'extract'
        self.btn_pause.setEnabled(True)
        self.files_found = db.get_total_count(self.db_conn)
        self.files_done = db.get_indexed_count(self.db_conn)
        self.lbl_title.setText('FileFinder — Indexing...')
        self.extractor = ExtractWorker(self._pause_event, self._cancel_event)
        self.extractor.file_done.connect(self._on_file_done)
        self.extractor.all_done.connect(self._on_all_done)
        self.extractor.start()

    def _start_watcher(self):
        try:
            roots = []
            for letter in 'CDEFGH':
                r = f'{letter}:\\'
                if os.path.exists(r):
                    roots.append(r)
            self._watcher = Observer()
            self._handler = PdfWatcher()
            for r in roots:
                self._watcher.schedule(self._handler, r, recursive=True)
            self._watcher.start()
        except Exception:
            pass

    def _on_scan_progress(self, count):
        self.files_found = count
        self.lbl_stats.setText(f'Found: {count:,}')

    def _on_scan_finished(self, total):
        self.files_found = total
        self.lbl_current.setText(f'Scan complete. {total:,} files found.')
        self._start_extraction()

    def _on_file_done(self, filename, has_text):
        self.files_done += 1
        if not has_text:
            self.files_empty += 1
        self.lbl_current.setText(filename)

    def _on_all_done(self):
        self.phase = 'done'
        self.files_found = db.get_total_count(self.db_conn)
        self.files_done = db.get_indexed_count(self.db_conn)
        self._refresh()
        self.lbl_title.setText('FileFinder — Done')
        self.lbl_current.setText('All files indexed.')
        self.btn_pause.setEnabled(False)
        self.btn_cancel.setText('Close')
        self._start_watcher()

    def _refresh(self):
        pending = max(0, self.files_found - self.files_done)
        if self.phase == 'scan':
            self.lbl_stats.setText(f'Found: {self.files_found:,}')
        else:
            self.lbl_stats.setText(
                f'Found: {self.files_found:,}  |  Done: {self.files_done:,}'
                f'  |  Pending: {pending:,}'
            )
        if self.files_found > 0:
            self.progress_bar.setRange(0, self.files_found)
            self.progress_bar.setValue(self.files_done)
        self.tray.setToolTip(
            f'FileFinder\nFound: {self.files_found:,}'
            f'\nDone: {self.files_done:,}\nPending: {pending:,}'
        )

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
        if self.phase == 'done':
            self._shutdown()
            return
        self._cancel_event.set()
        try:
            self._watcher.stop()
            self._watcher.join(timeout=2)
        except Exception:
            pass
        if hasattr(self, '_hotkey_listener'):
            self._hotkey_listener.stop()
        if hasattr(self, '_timer'):
            self._timer.stop()
        self._shutdown()

    def _shutdown(self):
        if self.db_conn:
            self.db_conn.close()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self.phase != 'done':
            self._hide_to_tray()
            event.ignore()
        else:
            self._cancel()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = IndexerWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
