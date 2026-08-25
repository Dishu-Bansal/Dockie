"""ui.py — Standalone PyQt6 search overlay for Dockie.

Reads the index database written by the backend and shows the same
Spotlight-style search UI: a translucent fullscreen overlay with a centered
search panel, highlighted scrollable results, and keyboard navigation.

Run with:  python ui.py [path-to-index.db]

DB resolution (first match wins):
  1. CLI argument
  2. DOCKIE_DB_PATH environment variable
  3. ~/.dockie/index.db   (where the backend keeps it)
"""

import datetime
import html
import os
import re
import sqlite3
import subprocess
import sys

from PyQt6.QtCore import (
    Qt,
    QTimer,
    QEasingCurve,
    QPropertyAnimation,
    QObject,
    QRunnable,
    QThreadPool,
    QPointF,
    QRectF,
    QSize,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# When launched without a console (pythonw.exe or a windowed PyInstaller
# build), Python leaves sys.stdout/stderr as None and print() would raise.
# Divert them so the overlay never crashes on logging.
if sys.stdout is None or sys.stderr is None:
    _null_stream = open(os.devnull, 'w')
    if sys.stdout is None:
        sys.stdout = _null_stream
    if sys.stderr is None:
        sys.stderr = _null_stream


# ---------------------------------------------------------------------------
# Configuration / colors
# ---------------------------------------------------------------------------

PANEL_RADIUS = 7
PANEL_WIDTH_RATIO = 0.35
RESULTS_MAX_HEIGHT = 300
SEARCH_DEBOUNCE_MS = 200
SEARCH_LIMIT = 50

# Overlay color constants (effective RGB once composited on the panel).
COLOR_OVERLAY_BLACK12 = QColor(0, 0, 0, 31)          # 12% black scrim
COLOR_PANEL_WHITE = QColor(255, 255, 255, 242)       # 95% white panel
COLOR_NOT_READY_BG = QColor(0xF8, 0xF8, 0xF8)        # not-ready panel
COLOR_SELECTED_ROW = QColor(0xE0, 0xE0, 0xE0)        # grey.shade300
COLOR_ROW_WHITE = QColor(255, 255, 255)
COLOR_FILENAME = QColor(0x21, 0x21, 0x21)             # black87 on white
COLOR_SECONDARY = QColor(0x75, 0x75, 0x75)            # black54 on white
COLOR_TERTIARY = QColor(0x8A, 0x8A, 0x8A)             # black38 on white
COLOR_HINT = QColor(0x9E, 0x9E, 0x9E)                 # placeholder
COLOR_PDF_ICON = QColor(0xF4, 0x43, 0x36)             # Material red
COLOR_DIVIDER = QColor(0, 0, 0, 31)                   # black12
COLOR_HOURGLASS = QColor(0x9E, 0x9E, 0x9E)            # Colors.grey

# Keys that dismiss the overlay.
DISMISS_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Meta,
    Qt.Key.Key_F1,
    Qt.Key.Key_F2,
    Qt.Key.Key_F3,
    Qt.Key.Key_F4,
    Qt.Key.Key_F5,
    Qt.Key.Key_F6,
    Qt.Key.Key_F7,
    Qt.Key.Key_F8,
    Qt.Key.Key_F9,
    Qt.Key.Key_F10,
    Qt.Key.Key_F11,
    Qt.Key.Key_F12,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_Insert,
    Qt.Key.Key_Delete,
    Qt.Key.Key_Print,
    Qt.Key.Key_ScrollLock,
    Qt.Key.Key_Pause,
}

HINT_TEXT = 'What file are you looking for?'
NOT_READY_TITLE = 'Index not ready'
NOT_READY_SUBTITLE = 'The PDF index is still being built. Please wait.'

# Content search drives off the FTS5 index (created by db._migrate_fts);
# SEARCH_SQL is the fallback for databases that predate FTS5. Both rank
# cheaply first and only then join back to `files` for the text of the
# final LIMIT rows, so matched fulltext never rides through the sort.
# Snippets for content matches are computed separately (FTS_SNIPPET_SQL)
# only for the rows that survive the LIMIT: FTS5's snippet() needs to
# read the matched rows' text, so computing it for every match (not just
# the top rows) dominates the query.
FTS_SEARCH_SQL = """
SELECT r.path, r.filename, COALESCE(f.text, '') AS fulltext, r.rank
FROM (
    SELECT path, filename, MIN(rank) AS rank
    FROM (
        SELECT f.path, f.filename, 1 AS rank
        FROM files f
        WHERE f.filename LIKE ?
        UNION ALL
        SELECT f.path, f.filename, 2 AS rank
        FROM files f
        WHERE f.filename LIKE ?
        UNION ALL
        SELECT f.path, f.filename, 3 AS rank
        FROM files_fts
        JOIN files f ON f.rowid = files_fts.rowid
        WHERE files_fts MATCH ?
    )
    GROUP BY path, filename
    ORDER BY rank, filename
    LIMIT ?
) r
JOIN files f ON f.path = r.path
ORDER BY r.rank, r.filename
"""

# Snippet around the match for the surviving content matches. Matched
# tokens (possibly stems, not the literal query) are wrapped in
# char(2)/char(3) markers so the UI can highlight exactly what matched.
FTS_SNIPPET_SQL = """
SELECT f.path, snippet(files_fts, 1, char(2), char(3), ' … ', 12)
FROM files_fts
JOIN files f ON f.rowid = files_fts.rowid
WHERE files_fts MATCH ?
  AND f.path IN (%s)
"""

SEARCH_SQL = """
SELECT r.path, r.filename, COALESCE(f.text, '') AS fulltext, r.rank
FROM (
    SELECT path, filename,
           CASE
               WHEN filename LIKE ? THEN 1
               WHEN filename LIKE ? THEN 2
               WHEN text IS NOT NULL AND text LIKE ? THEN 3
               ELSE 4
           END AS rank
    FROM files
    WHERE filename LIKE ?
       OR (text IS NOT NULL AND text LIKE ?)
    ORDER BY rank, filename
    LIMIT ?
) r
JOIN files f ON f.path = r.path
ORDER BY r.rank, r.filename
"""


# ---------------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------------

def db_path(argv=None):
    if argv is not None and len(argv) > 1:
        return argv[1]
    if os.environ.get('DOCKIE_DB_PATH'):
        return os.environ['DOCKIE_DB_PATH']
    return os.path.join(os.path.expanduser('~'), '.dockie', 'index.db')


def db_ready():
    return os.path.exists(db_path())


def fts5_match(query):
    """Build a safe FTS5 MATCH expression from free-text input.

    Each whitespace-separated term is quoted (embedded quotes doubled) so
    FTS5 operators ('-', '*', ':', parentheses, ...) typed by the user are
    treated literally; terms are ANDed, which is FTS5's default connector
    for a space-separated list."""
    return ' '.join(f'"{term.replace(chr(34), chr(34) * 2)}"'
                    for term in query.split())


def _fetch_snippets(conn, match, paths):
    """FTS5 snippets for the given content-matched paths: {path: snippet}.

    snippet() re-tokenizes the matched rows' text, so it is restricted to
    the rows the literal-find snippet cannot explain (search() passes only
    the surviving rank-3 paths it failed on). Matched tokens are wrapped
    in \\x02/\\x03 markers."""
    if not paths:
        return {}
    marks = ','.join('?' for _ in paths)
    return dict(conn.execute(
        FTS_SNIPPET_SQL % marks, (match, *paths)).fetchall())


def _fts_available(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='files_fts'"
    ).fetchone()
    return row is not None


def search(query):
    """Run a query against the index DB. Returns a list of
    (path, filename, snippet, rank) tuples, or None if the DB is missing.
    The snippet is FTS5's own fragment around the match (matched tokens
    wrapped in \x02/\x03 markers for highlighting), falling back to a
    literal-find snippet for filename matches and pre-FTS5 databases."""
    q = query.strip()
    if not q:
        return []
    if not db_ready():
        return None
    prefix = f'{q}%'
    contains = f'%{q}%'
    match = fts5_match(q)
    try:
        # Fresh connection per query so we always see the latest committed
        # rows (the backend writes from another process).
        conn = sqlite3.connect(db_path())
        try:
            if _fts_available(conn):
                fts_path = True
                rows = conn.execute(
                    FTS_SEARCH_SQL,
                    (prefix, contains, match, SEARCH_LIMIT),
                ).fetchall()
                # Literal-find snippets are cheap; FTS5's snippet() has to
                # re-tokenize the matched row's text, so reserve it for
                # rows the literal find cannot explain (stems, multi-word
                # AND, filename-column matches).
                snips = {}
                need_fts = []
                for path, filename, fulltext, rank in rows:
                    if rank == 3:
                        lit = make_snippet(fulltext, q)
                        if lit:
                            snips[path] = lit
                        else:
                            need_fts.append(path)
                snips.update(_fetch_snippets(conn, match, need_fts))
            else:
                fts_path = False
                rows = conn.execute(
                    SEARCH_SQL,
                    (prefix, contains, contains, contains, contains,
                     SEARCH_LIMIT),
                ).fetchall()
                snips = {}
        finally:
            conn.close()
        results = []
        for path, filename, fulltext, rank in rows:
            # Rank 3 = content match: prefer FTS5's marked snippet, then
            # the literal-find one. Filename matches and the LIKE fallback
            # keep the literal-find snippet (may be empty when the text has
            # no match).
            snippet = (snips.get(path) or '') if fts_path and rank == 3 else (
                make_snippet(fulltext, q) if rank <= 3 else '')
            results.append((path, filename, snippet, rank))
        return results
    except Exception:
        return []


def make_snippet(text, query, context=80):
    """~context chars around the first query match, newlines flattened,
    ellipses when truncated."""
    if not text or not query:
        return ''
    idx = text.lower().find(query.lower())
    if idx == -1:
        return ''
    start = max(0, idx - context // 2)
    end = min(len(text), idx + len(query) + context // 2)
    snip = text[start:end].replace('\n', ' ').strip()
    if start > 0:
        snip = '\u2026' + snip
    if end < len(text):
        snip = snip + '\u2026'
    return snip


# ---------------------------------------------------------------------------
# Async search (worker thread)
# ---------------------------------------------------------------------------

class _SearchSignals(QObject):
    """Signals from a search worker to the overlay (main thread)."""
    finished = pyqtSignal(int, object)  # generation, results (list | None)
    failed = pyqtSignal(int, str)       # generation, error message


class _SearchWorker(QRunnable):
    """Runs one SQLite query on a QThreadPool thread so the Qt main thread
    (and with it the overlay, typing, animations) never blocks on the DB."""

    def __init__(self, generation, query):
        super().__init__()
        self.generation = generation
        self.query = query
        self.signals = _SearchSignals()

    def run(self):
        try:
            results = search(self.query)
        except Exception as exc:
            self.signals.failed.emit(self.generation, str(exc))
            return
        self.signals.finished.emit(self.generation, results)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _wrap_text(text, font, max_width):
    """Break text into lines at word boundaries (character fallback), each
    fitting within max_width."""
    fm = QFontMetrics(font)
    lines = []
    current = ''
    for word in text.split(' '):
        candidate = word if not current else current + ' ' + word
        if fm.horizontalAdvance(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ''
        if fm.horizontalAdvance(word) > max_width:
            # A single over-long token: hard-break it char by char.
            prefix = ''
            for ch in word:
                if fm.horizontalAdvance(prefix + ch) <= max_width:
                    prefix += ch
                else:
                    lines.append(prefix)
                    prefix = ch
            current = prefix
        else:
            current = word
    if current:
        lines.append(current)
    return lines


def _clip_lines(text, font, max_width, max_lines):
    """Return up to max_lines lines; the last line is elided with a trailing
    ellipsis when the text overflows."""
    lines = _wrap_text(text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    keep = lines[:max_lines]
    fm = QFontMetrics(font)
    ellipsis = '\u2026'
    ellipsis_w = fm.horizontalAdvance(ellipsis)
    last = keep[-1]
    while (fm.horizontalAdvance(last) > max_width - ellipsis_w
           and len(last) > 1):
        last = last[:-1]
    keep[-1] = last + ellipsis
    return keep


_HIGHLIGHT_SPAN = ('<span style="background-color:#FFEB3B;color:#000000;'
                   'font-weight:600;">')
_MARKER_RE = re.compile(r'\x02(.*?)\x03')


def _highlight_html(text, query):
    """Escape text and wrap matches in the overlay highlight span.

    FTS5 snippet() output wraps each actual matched token — possibly a
    stem rather than the literal query — in \x02...\x03; when present,
    those markers are rendered as the highlight. Otherwise every
    case-insensitive literal query match is highlighted."""
    if '\x02' in text and '\x03' in text:
        return _MARKER_RE.sub(_HIGHLIGHT_SPAN + r'\1</span>',
                              html.escape(text))
    if not query:
        return html.escape(text)
    out = []
    lower = text.lower()
    q = query.lower()
    start = 0
    while True:
        idx = lower.find(q, start)
        if idx == -1:
            out.append(html.escape(text[start:]))
            break
        out.append(html.escape(text[start:idx]))
        out.append(f'{_HIGHLIGHT_SPAN}{html.escape(text[idx:idx + len(q)])}'
                   f'</span>')
        start = idx + len(q)
    return ''.join(out)


# ---------------------------------------------------------------------------
# Small painted widgets (icon stand-ins for the Material icons)
# ---------------------------------------------------------------------------

class _SearchIcon(QWidget):
    """Magnifier glyph, 20 px, black (Icons.search)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(0, 0, 0), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2.5, 2.5, 11, 11))
        p.drawLine(QPointF(11.5, 11.5), QPointF(17.5, 17.5))


class _PdfIcon(QWidget):
    """PDF document glyph, 28 px, red (Icons.picture_as_pdf)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        red = COLOR_PDF_ICON
        body = QPainterPath()
        body.moveTo(3, 2)
        body.lineTo(17, 2)
        body.lineTo(25, 10)
        body.lineTo(25, 26)
        body.lineTo(3, 26)
        body.closeSubpath()
        p.setPen(QPen(red, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(body)
        # Fold crease.
        p.drawLine(17, 2, 17, 10)
        p.drawLine(17, 10, 25, 10)
        # 'PDF' label.
        f = QFont(self.font())
        f.setPixelSize(8)
        f.setBold(True)
        p.setFont(f)
        p.setPen(red)
        p.drawText(QRectF(3, 12, 22, 14), Qt.AlignmentFlag.AlignCenter, 'PDF')


class _Divider(QWidget):
    """1 px black12 line (Material Divider)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), COLOR_DIVIDER)


class _HourglassIcon(QWidget):
    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(COLOR_HOURGLASS, 1.6)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        w = self.width()
        mid = w // 2
        p.drawLine(3, 2, w - 3, 2)
        p.drawLine(3, 2, 3, mid - 1)
        p.drawLine(w - 3, 2, w - 3, mid - 1)
        p.drawLine(3, mid - 1, w - 3, mid - 1)
        p.drawLine(3, mid + 1, w - 3, mid + 1)
        p.drawLine(3, mid + 1, 3, w - 2)
        p.drawLine(w - 3, mid + 1, w - 3, w - 2)
        p.drawLine(3, w - 2, w - 3, w - 2)


class _CornerCutout(QWidget):
    """Rounded-corner patch painted over child content at a panel corner
    (matches the panel container's anti-aliased clipping)."""

    def __init__(self, corner, parent=None):
        super().__init__(parent)
        self._corner = corner  # 'bl' | 'br'
        self.setFixedSize(PANEL_RADIUS, PANEL_RADIUS)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if self._corner == 'bl':
            circle = QRectF(-PANEL_RADIUS, -PANEL_RADIUS,
                            PANEL_RADIUS * 2, PANEL_RADIUS * 2)
        else:  # 'br'
            circle = QRectF(self.width() - PANEL_RADIUS, -PANEL_RADIUS,
                            PANEL_RADIUS * 2, PANEL_RADIUS * 2)
        path.addEllipse(circle)
        path.setFillRule(Qt.FillRule.OddEvenFill)
        p.fillPath(path, COLOR_PANEL_WHITE)


def _font(size_px, weight=QFont.Weight.Normal):
    f = QFont()
    f.setPixelSize(size_px)
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------------------
# Result row
# ---------------------------------------------------------------------------

class _RichLabel(QLabel):
    """Rich-text label: query matches highlighted, content wrapped into
    pre-computed lines joined with <br/>."""

    def __init__(self, color, size_px, weight=QFont.Weight.Normal,
                 parent=None):
        super().__init__(parent)
        self._lines = []
        self._query = ''
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWordWrap(False)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFont(_font(size_px, weight))
        self.setStyleSheet(
            f'color: {color.name()}; background: transparent;'
        )

    def set_lines(self, lines, query=''):
        self._lines = list(lines)
        self._query = query
        if not self._lines:
            self.setText('')
            self.setFixedHeight(QFontMetrics(self.font()).lineSpacing())
            return
        html_text = '<br/>'.join(
            _highlight_html(line, self._query) for line in self._lines)
        self.setText(html_text)
        fm = QFontMetrics(self.font())
        self.setFixedHeight(fm.lineSpacing() * len(self._lines))


class _ResultRow(QWidget):
    def __init__(self, result, query, panel_width, on_activate, parent=None):
        """result: (path, filename, snippet, rank)."""
        super().__init__(parent)
        self._result = result
        self._query = query
        self._selected = False
        self._on_activate = on_activate
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        path, filename, snippet, rank = result
        text_width = max(60, panel_width - 12 * 2 - 28 - 12)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)

        row.addWidget(_PdfIcon(), 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        row.addLayout(col, 1)

        self._filename_label = _RichLabel(COLOR_FILENAME, 16,
                                          QFont.Weight.DemiBold)
        self._path_label = _RichLabel(COLOR_SECONDARY, 13)
        col.addWidget(self._filename_label)
        col.addWidget(self._path_label)
        self._snippet_label = None
        if snippet:
            self._snippet_label = _RichLabel(COLOR_SECONDARY, 13)
            col.addWidget(self._snippet_label)
            self._snippet_label.set_lines(
                _clip_lines(snippet, self._snippet_label.font(),
                            text_width, 2), query)
        self._filename_label.set_lines(
            _clip_lines(filename, self._filename_label.font(),
                        text_width, 1), query)
        self._path_label.set_lines(
            _clip_lines(path, self._path_label.font(), text_width, 1), '')
        # Rows have fixed content: derive an exact height so the list can be
        # sized without relying on layout activation (QLayout.sizeHint is
        # empty until the widget has been given a geometry once).
        col_height = (self._filename_label.height() + 3
                      + self._path_label.height())
        if self._snippet_label is not None:
            col_height += 3 + self._snippet_label.height()
        self.setFixedHeight(12 + max(28, col_height) + 12)

    def set_selected(self, selected):
        if selected != self._selected:
            self._selected = selected
            self.update()

    def result_path(self):
        return self._result[0]

    def mousePressEvent(self, event):
        self._on_activate(self)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),
                   COLOR_SELECTED_ROW if self._selected else COLOR_ROW_WHITE)


# ---------------------------------------------------------------------------
# Search panel
# ---------------------------------------------------------------------------

class _ResultsArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMaximumHeight(RESULTS_MAX_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Minimum)
        self.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }'
            'QScrollArea > QWidget > QWidget { background: transparent; }'
            'QScrollBar:vertical { background: transparent; width: 6px;'
            ' margin: 0px; border: none; }'
            'QScrollBar::handle:vertical { background: #BDBDBD;'
            ' border-radius: 3px; min-height: 20px; }'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical'
            ' { height: 0px; }'
            'QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical'
            ' { background: transparent; }'
        )
        self._content = QWidget()
        self._content.setAutoFillBackground(False)
        self._content.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._rows_layout = QVBoxLayout(self._content)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch(1)
        self.setWidget(self._content)

    def clear_rows(self):
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def add_row(self, row):
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)

    def sizeHint(self):
        # QScrollArea does not size itself to its contents by default; report
        # the content's height so the panel grows with the result rows.
        content = self.widget()
        if content is None:
            return super().sizeHint()
        sb = self.verticalScrollBar().sizeHint().width()
        return QSize(max(super().sizeHint().width(), content.width() + sb),
                     min(content.height(), self.maximumHeight()))

    def row_at(self, index):
        if index < 0 or index >= self._rows_layout.count() - 1:
            return None
        item = self._rows_layout.itemAt(index)
        return item.widget() if item is not None else None


class _NotReadyPanel(QWidget):
    """Compact status block shown under the search bar when the index is
    still being built (mirrors _buildNotReadyStatus)."""

    def __init__(self, panel_width, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(panel_width)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 18, 16, 18)
        v.setSpacing(0)
        v.addWidget(_HourglassIcon(18), 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(8)
        title = QLabel(NOT_READY_TITLE)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f'color: {COLOR_SECONDARY.name()};'
                            f'background: transparent;')
        title.setFont(_font(13))
        v.addWidget(title)
        v.addSpacing(4)
        subtitle = QLabel(NOT_READY_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f'color: {COLOR_TERTIARY.name()};'
                               f'background: transparent;')
        subtitle.setFont(_font(11))
        v.addWidget(subtitle)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        # Square top corners, rounded bottom corners (BorderRadius.vertical
        # bottom only).
        path.moveTo(r.left(), r.top())
        path.lineTo(r.right(), r.top())
        path.lineTo(r.right(), r.bottom() - PANEL_RADIUS)
        path.quadTo(r.right(), r.bottom(),
                    r.right() - PANEL_RADIUS, r.bottom())
        path.lineTo(r.left() + PANEL_RADIUS, r.bottom())
        path.quadTo(r.left(), r.bottom(), r.left(),
                    r.bottom() - PANEL_RADIUS)
        path.closeSubpath()
        p.fillPath(path, COLOR_NOT_READY_BG)


class _SpotlightPanel(QWidget):
    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self._overlay = overlay
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)
        screen = QApplication.primaryScreen()
        width = max(240, int(screen.geometry().width() * PANEL_WIDTH_RATIO))
        self.setFixedWidth(width)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # --- Search bar row: input left, magnifier right ---
        search_row = QWidget()
        search_row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        search_row.setAutoFillBackground(False)
        h = QHBoxLayout(search_row)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(8)
        self.edit = _SearchEdit(overlay)
        self.edit.setFont(_font(15))
        self.edit.setPlaceholderText(HINT_TEXT)
        self.edit.setStyleSheet(
            'QLineEdit { border: none; background: transparent;'
            f' color: {COLOR_FILENAME.name()}; padding: 0px; }}'
            f'QLineEdit::placeholder {{ color: {COLOR_HINT.name()}; }}'
        )
        h.addWidget(self.edit, 1)
        h.addWidget(_SearchIcon(), 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(search_row)

        # --- Divider between search bar and results ---
        self.divider = _Divider()
        self.divider.hide()
        v.addWidget(self.divider)

        # --- Results list ---
        self.results_area = _ResultsArea()
        self.results_area.hide()
        v.addWidget(self.results_area)

        # Corner cutouts that restore the rounded panel corners on top of the
        # list content (clipped to the panel's rounded shape).
        self._cutout_bl = _CornerCutout('bl', self)
        self._cutout_br = _CornerCutout('br', self)
        self._cutout_bl.raise_()
        self._cutout_br.raise_()

        self._results = []
        self._selected_index = -1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cutout_bl.move(0, self.height() - PANEL_RADIUS)
        self._cutout_br.move(self.width() - PANEL_RADIUS,
                             self.height() - PANEL_RADIUS)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_PANEL_WHITE)
        p.drawRoundedRect(QRectF(self.rect()), PANEL_RADIUS, PANEL_RADIUS)

    # --- results management ---

    def set_results(self, results, query):
        self._results = results
        self._selected_index = 0 if results else -1
        self.results_area.clear_rows()
        panel_width = self.width()
        rows = []
        for i, result in enumerate(results):
            row = _ResultRow(
                result, query, panel_width,
                lambda r: self._overlay.activate_result(r),
                parent=self.results_area._content,
            )
            row.set_selected(i == 0)
            self.results_area.add_row(row)
            rows.append(row)
        if results:
            self.divider.show()
            self.results_area.show()
            content = self.results_area._content
            total_height = sum(row.height() for row in rows)
            content.setFixedSize(self.width(), total_height)
            content.layout().activate()
        else:
            self.divider.hide()
            self.results_area.hide()
        self.results_area.verticalScrollBar().setValue(0)
        # Force the panel layout to re-query size hints (the layout caches
        # item sizes from its last activation).
        self.results_area.updateGeometry()
        self.layout().invalidate()
        self.adjustSize()

    def select_next(self):
        if not self._results:
            return
        self._set_selected(min(self._selected_index + 1,
                               len(self._results) - 1))

    def select_prev(self):
        if not self._results:
            return
        self._set_selected(max(self._selected_index - 1, 0))

    def _set_selected(self, index):
        old = self.results_area.row_at(self._selected_index)
        if old is not None:
            old.set_selected(False)
        self._selected_index = index
        new = self.results_area.row_at(index)
        if new is not None:
            new.set_selected(True)
            self._center_row(new)

    def _center_row(self, row):
        sb = self.results_area.verticalScrollBar()
        viewport_h = self.results_area.viewport().height()
        target = row.y() + row.height() // 2 - viewport_h // 2
        sb.setValue(max(0, min(target, sb.maximum())))

    def selected_path(self):
        if 0 <= self._selected_index < len(self._results):
            return self._results[self._selected_index][0]
        return None


# ---------------------------------------------------------------------------
# Search edit (navigation, activation, and dismiss keys)
# ---------------------------------------------------------------------------

class _SearchEdit(QLineEdit):
    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self._overlay = overlay

    def keyPressEvent(self, event):
        key = event.key()
        if key in DISMISS_KEYS:
            self._overlay.log(f'Dismiss key: {key}')
            self._overlay.close_app()
            return
        if key == Qt.Key.Key_Escape:
            self._overlay.log('Dismiss key: Escape')
            self._overlay.close_app()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shift = bool(event.modifiers()
                         & Qt.KeyboardModifier.ShiftModifier)
            self._overlay.log(f'Enter pressed (shift={shift})')
            self._overlay.activate_selected(location=shift)
            return
        if key == Qt.Key.Key_Down:
            self._overlay.panel.select_next()
            return
        if key == Qt.Key.Key_Up:
            self._overlay.panel.select_prev()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Overlay window
# ---------------------------------------------------------------------------

class SearchOverlay(QWidget):
    closed = pyqtSignal()  # emitted when the overlay closes (any dismissal)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._quit_on_close = True  # standalone: closing quits the app
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle('Dockie Spotlight Search')

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.addStretch(1)
        center = QHBoxLayout()
        center.addStretch(1)
        self.panel = _SpotlightPanel(self)
        center.addWidget(self.panel)
        center.addStretch(1)
        root.addLayout(center)
        root.addStretch(1)

        # Search fires only after the user pauses typing: every keystroke
        # restarts this single-shot timer (see _schedule_search), so the
        # query runs at most once per SEARCH_DEBOUNCE_MS of silence.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._perform_search)
        self.panel.edit.textChanged.connect(self._on_search_changed)

        # Async search: queries run on QThreadPool threads; a generation
        # counter tags each query so a slow, outdated result is discarded
        # instead of overwriting the results of a newer keystroke.
        self._search_pool = QThreadPool.globalInstance()
        self._search_generation = 0
        self._search_workers = {}  # generation -> worker, alive until handled

        self._not_ready = None
        if not db_ready():
            self.log(f'DB not found at {db_path()}; showing index-not-ready',
                     'WARN')
            self._show_not_ready()
        else:
            self.log(f'DB ready: {db_path()}')

    # --- logging ---

    def log(self, message, level='INFO'):
        t = datetime.datetime.now()
        line = (f'[{t.year}-{str(t.month).zfill(2)}-{str(t.day).zfill(2)} '
                f'{str(t.hour).zfill(2)}:{str(t.minute).zfill(2)}:'
                f'{str(t.second).zfill(2)}] '
                f'[{level}] {message}')
        # flush=True keeps output streaming when stdout is a pipe (the
        # backend tails the overlay's output as "ui: ..." log lines).
        print(line, flush=True)

    # --- not-ready state ---

    def _show_not_ready(self):
        self.panel.results_area.hide()
        self.panel.divider.hide()
        self._not_ready = _NotReadyPanel(self.panel.width(), self.panel)
        # Insert below the search row (the divider/area stay hidden).
        panel_layout = self.panel.layout()
        panel_layout.insertWidget(panel_layout.count() - 1, self._not_ready)

    # --- lifecycle ---

    def set_quit_on_close(self, quit_app: bool):
        """When embedded in the backend (in-process), closing the overlay
        must only hide it — never quit the application that owns it."""
        self._quit_on_close = quit_app

    def close_app(self):
        print('Closing overlay')
        self.close()
        if self._quit_on_close:
            QApplication.instance().quit()

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()

    def show_with_fade(self):
        self.showFullScreen()
        self._grab_focus()
        # Re-grab once the window is fully mapped (some platforms only
        # activate after the first pass through the event loop).
        QTimer.singleShot(0, self._grab_focus)
        self._fade = QPropertyAnimation(self, b'windowOpacity', self)
        self._fade.setDuration(500)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.start()

    def _grab_focus(self):
        self.raise_()
        self.activateWindow()
        self.panel.edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        if sys.platform == 'win32':
            self._force_windows_foreground()

    def _force_windows_foreground(self):
        """Give the overlay the OS input focus.

        The foreground lock normally stops background windows from taking
        focus, but this process owns the low-level keyboard hook (pynput)
        that just handled the summon keystrokes, so SetForegroundWindow is
        permitted. The synthetic Alt tap additionally clears the
        foreground-lock timeout for edge cases (e.g. a tray app that has
        never been foreground).
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            if not user32.SetForegroundWindow(hwnd):
                user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
                user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU up
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
        except Exception:
            pass

    # --- search ---

    def _on_search_changed(self, text):
        # Every keystroke advances the generation, immediately invalidating
        # any in-flight query for older text.
        self._search_generation += 1
        if not text.strip():
            self._debounce.stop()
            self.panel.set_results([], '')
            return
        self._schedule_search()

    def _schedule_search(self):
        """(Re)start the pause timer.

        A query only fires once the user has been idle for
        SEARCH_DEBOUNCE_MS — never on every keystroke. This is the single
        scheduling entry point the async pipeline hangs off.
        """
        self._debounce.start()

    def _perform_search(self):
        query = self.panel.edit.text().strip()
        if not query:
            return
        generation = self._search_generation
        worker = _SearchWorker(generation, query)
        worker.signals.finished.connect(self._on_search_finished)
        worker.signals.failed.connect(self._on_search_failed)
        self._search_workers[generation] = worker
        self._search_pool.start(worker)

    def _on_search_finished(self, generation, results):
        self._search_workers.pop(generation, None)
        if generation != self._search_generation:
            print(f'Search: discarding stale results (gen {generation})')
            return
        query = self.panel.edit.text().strip()
        if results is None:
            print(f'Search: DB missing for query "{query}"')
            self.panel.set_results([], '')
            return
        print(f'Search: "{query}" -> {len(results)} results')
        self.panel.set_results(results, query)

    def _on_search_failed(self, generation, message):
        self._search_workers.pop(generation, None)
        if generation != self._search_generation:
            return
        print(f'Search failed: {message}')

    # --- actions ---

    def activate_selected(self, location=False):
        path = self.panel.selected_path()
        if path:
            print(f'Activate index={self.panel._selected_index} '
                  f'location={location} path={path}')
            if location:
                self._reveal_in_explorer(path)
            else:
                self._open_file(path)
        self.close_app()

    def activate_result(self, row):
        path = row.result_path()
        print(f'Activate result path={path}')
        self._open_file(path)
        self.close_app()

    def _open_file(self, path):
        print(f'Open file: {path}')
        try:
            subprocess.Popen(['cmd', '/c', 'start', '', path])
        except Exception as e:
            print(f'Open file failed: {path}: {e}')

    def _reveal_in_explorer(self, path):
        print(f'Reveal in Explorer: {path}')
        try:
            subprocess.Popen(['explorer', '/select,', path])
        except Exception as e:
            print(f'Reveal in Explorer failed: {path}: {e}')

    # --- overlay behavior ---

    def mousePressEvent(self, event):
        # Any click outside the panel dismisses the overlay (the panel's own
        # children consume clicks inside it).
        if not self.panel.geometry().contains(event.position().toPoint()):
            print('Overlay dismissed (outside click)')
            self.close_app()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), COLOR_OVERLAY_BLACK12)


def main(argv=None):
    if argv is None:
        argv = sys.argv
    app = QApplication(argv)
    app.setApplicationName('Dockie UI')
    overlay = SearchOverlay()
    overlay.show_with_fade()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
