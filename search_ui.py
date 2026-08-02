"""
Spotlight-style search bar — opens on F-key triple-tap, overlays all windows.
Queries the indexed DB as the user types, shows ranked results.
"""

import os
import re
import subprocess
import sys
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QKeyEvent, QColor, QPalette

from pynput import keyboard

import db


# ── Signals bridge ──
class SearchBridge(QObject):
    show_signal = pyqtSignal()
    hide_signal = pyqtSignal()


# ── Search Bar ──
class SearchBar(QWidget):
    SEARCH_DELAY = 150  # ms debounce

    def __init__(self):
        super().__init__()
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._do_search)

        self._init_ui()
        self._position()

    def _init_ui(self):
        self.setWindowTitle('FileFinder')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setFixedWidth(580)
        self.setMinimumHeight(60)
        self.setMaximumHeight(520)

        self.setStyleSheet('''
            QWidget#SearchBar {
                background-color: #1e1e2e;
                border: 1px solid #45475a;
                border-radius: 12px;
            }
            QLineEdit {
                background-color: transparent;
                border: none;
                color: #cdd6f4;
                font-family: "Segoe UI", sans-serif;
                font-size: 20px;
                padding: 10px 16px;
            }
            QListWidget {
                background-color: transparent;
                border: none;
                border-top: 1px solid #313244;
                color: #cdd6f4;
                font-family: "Segoe UI", sans-serif;
                font-size: 13px;
                outline: none;
            }
            QListWidget::item {
                padding: 8px 16px;
                border-bottom: 1px solid #313244;
                min-height: 50px;
            }
            QListWidget::item:selected {
                background-color: #45475a;
                border-radius: 3px;
            }
            QLabel { color: #a6adc8; background: transparent; }
        ''')
        self.setObjectName('SearchBar')

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.input = QLineEdit()
        self.input.setPlaceholderText('Search PDFs…')
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self._on_enter)
        layout.addWidget(self.input)

        self.results = QListWidget()
        self.results.setVisible(False)
        self.results.itemActivated.connect(self._on_enter)
        layout.addWidget(self.results)

        self.setLayout(layout)

    def _position(self):
        screen = QApplication.primaryScreen().availableGeometry()
        w = self.width()
        h = self.height()
        x = screen.left() + (screen.width() - w) // 2
        y = int(screen.top() + screen.height() * 0.28)
        self.move(x, y)

    def show_and_focus(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.clear()
        self.results.clear()
        self.results.setVisible(False)
        self.setFixedHeight(60)
        self._position()
        self.input.setFocus()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        if event.key() == Qt.Key.Key_Down:
            if self.results.count() > 0:
                self.results.setFocus()
                self.results.setCurrentRow(0)
            return
        super().keyPressEvent(event)

    def _on_text_changed(self, text):
        self._debounce_timer.start(self.SEARCH_DELAY)

    def _do_search(self):
        query = self.input.text().strip()
        if len(query) < 2:
            self.results.clear()
            self.results.setVisible(False)
            self.setFixedHeight(60)
            return

        conn = db.get_conn()
        try:
            rows = db.search(conn, query, limit=15)
        finally:
            conn.close()

        self.results.clear()
        if not rows:
            item = QListWidgetItem('No results found')
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(QColor('#6c7086'))
            self.results.addItem(item)
        else:
            for path, filename, snippet, rank in rows:
                w = QWidget()
                wl = QVBoxLayout()
                wl.setContentsMargins(0, 2, 0, 2)
                wl.setSpacing(1)

                name_lbl = QLabel(self._highlight(filename, self.input.text()))
                name_lbl.setFont(QFont('Segoe UI', 12, QFont.Weight.Medium))
                name_lbl.setStyleSheet('color: #cdd6f4;')
                name_lbl.setTextFormat(Qt.TextFormat.RichText)
                wl.addWidget(name_lbl)

                if snippet:
                    snip_lbl = QLabel(self._highlight(snippet, self.input.text()))
                    snip_lbl.setFont(QFont('Segoe UI', 10))
                    snip_lbl.setWordWrap(True)
                    snip_lbl.setMaximumHeight(36)
                    snip_lbl.setTextFormat(Qt.TextFormat.RichText)
                    wl.addWidget(snip_lbl)
                else:
                    spacer = QLabel('')
                    spacer.setFixedHeight(4)
                    wl.addWidget(spacer)

                w.setLayout(wl)

                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setSizeHint(w.sizeHint())
                self.results.addItem(item)
                self.results.setItemWidget(item, w)

        self.results.setVisible(True)
        self.setFixedHeight(min(520, 60 + self.results.count() * 72 + 10))

    @staticmethod
    def _highlight(text, query):
        """Wrap all occurrences of query in <b> tags for bold display."""
        q = query.strip()
        if not q or not text:
            return text
        safe = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        q_safe = q.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        escaped = re.escape(q_safe)
        return re.sub(
            f'({escaped})',
            r'<b style="color:#fab387;">\1</b>',
            safe,
            flags=re.IGNORECASE
        )

    def _on_enter(self):
        if self.results.count() == 0:
            return
        item = self.results.currentItem()
        if item is None:
            item = self.results.item(0)
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if path and os.path.exists(path):
            subprocess.Popen(['start', '', path], shell=True)
        self.hide()


# ── Hotkey Listener ──
_bridge: SearchBridge | None = None
_last_f_times: list[float] = []


def _on_press(key):
    global _last_f_times, _bridge
    try:
        if hasattr(key, 'char') and key.char and key.char.lower() == 'f':
            pass
        else:
            return
    except Exception:
        try:
            if key == keyboard.Key.f:
                pass
            else:
                return
        except Exception:
            return

    now = __import__('time').time()
    _last_f_times.append(now)
    # Keep only presses within last 1 second
    cutoff = now - 1.0
    _last_f_times = [t for t in _last_f_times if t > cutoff]

    if len(_last_f_times) >= 3 and _bridge:
        _last_f_times.clear()
        _bridge.show_signal.emit()


def start_listener(bridge: SearchBridge):
    global _bridge
    _bridge = bridge
    listener = keyboard.Listener(on_press=_on_press)
    listener.daemon = True
    listener.start()
    return listener
