"""Database layer — SQLite storage for indexed files."""

import os
import sqlite3
import sys
import time

import applog


def _data_dir():
    """Where app data lives: always the per-user ~/.dockie dir.

    A Program Files install is read-only for normal (non-elevated) runs, so
    the old probe would silently switch the DB between the install dir and
    the user dir depending on elevation — leaving the log/DB in neither
    expected place and splitting data across two locations. A single
    per-user dir keeps the DB and log in one predictable place for every
    launch."""
    return os.path.join(os.path.expanduser('~'), '.dockie')


DATA_DIR = _data_dir()
DB_DIR = DATA_DIR
DB_PATH = os.path.join(DB_DIR, 'index.db')


def get_conn():
    """Return a connection. Each thread must call this — SQLite connections are NOT thread-safe."""
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn
    except Exception:
        applog.log_exc(f'DB: failed to open {DB_PATH}')
        raise


def init_db(conn):
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS files (
            path      TEXT PRIMARY KEY,
            filename  TEXT,
            text      TEXT,
            size      INTEGER,
            modified  REAL,
            scanned_at REAL,
            indexed_at REAL
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_files_text ON files(text)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_files_scanned ON files(scanned_at)')
        conn.commit()
    except Exception:
        applog.log_exc('DB: failed to initialize schema')
        raise


def insert_scan_result(conn, path):
    """Insert a newly scanned PDF path. Only inserts if not already present."""
    filename = os.path.basename(path)
    try:
        stat = os.stat(path)
        size = stat.st_size
        modified = stat.st_mtime
    except OSError:
        return
    now = time.time()
    conn.execute(
        '''INSERT OR IGNORE INTO files (path, filename, size, modified, scanned_at)
           VALUES (?, ?, ?, ?, ?)''',
        (path, filename, size, modified, now)
    )


def get_pending_count(conn):
    """Number of files whose text column is NULL (not yet extracted)."""
    row = conn.execute('SELECT COUNT(*) FROM files WHERE text IS NULL').fetchone()
    return row[0]


def get_pending_batch(conn, limit=50):
    """Yield up to `limit` (path, filename) pairs that need extraction."""
    rows = conn.execute(
        'SELECT path, filename FROM files WHERE text IS NULL LIMIT ?', (limit,)
    ).fetchall()
    return rows


def mark_extracted(conn, path, text):
    """Update a file with extracted text."""
    now = time.time()
    conn.execute(
        'UPDATE files SET text = ?, indexed_at = ? WHERE path = ?',
        (text, now, path)
    )


def mark_deleted(conn, path):
    conn.execute('DELETE FROM files WHERE path = ?', (path,))


def mark_pending(conn, path, size=None, modified=None):
    """Mark a file as needing (re)extraction, refreshing its size/mtime.

    When size/modified are omitted they are read from disk; pass them to avoid
    a redundant os.stat when the caller already has fresh values."""
    if size is None or modified is None:
        try:
            st = os.stat(path)
            size = st.st_size
            modified = st.st_mtime
        except OSError:
            return
    conn.execute(
        'UPDATE files SET text = NULL, size = ?, modified = ?, scanned_at = ? WHERE path = ?',
        (size, modified, time.time(), path),
    )


def move_file(conn, old_path, new_path):
    """Rename/move a tracked file, preserving its extracted text and metadata."""
    row = conn.execute(
        'SELECT text, size, modified, scanned_at, indexed_at FROM files WHERE path = ?',
        (old_path,),
    ).fetchone()
    if row is None:
        insert_scan_result(conn, new_path)
        return
    conn.execute('DELETE FROM files WHERE path = ?', (old_path,))
    conn.execute(
        '''INSERT OR REPLACE INTO files
           (path, filename, text, size, modified, scanned_at, indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (new_path, os.path.basename(new_path), row[0], row[1], row[2], row[3], row[4]),
    )


def get_all_paths(conn):
    """Return set of all file paths in DB for diff-on-restart."""
    rows = conn.execute('SELECT path FROM files').fetchall()
    return {r[0] for r in rows}


def get_total_count(conn):
    row = conn.execute('SELECT COUNT(*) FROM files').fetchone()
    return row[0]


def get_indexed_count(conn):
    row = conn.execute('SELECT COUNT(*) FROM files WHERE text IS NOT NULL').fetchone()
    return row[0]


def file_exists(conn, path):
    row = conn.execute('SELECT 1 FROM files WHERE path = ?', (path,)).fetchone()
    return row is not None


def search(conn, query, limit=20):
    """Search indexed files by filename and content. Returns ranked results.
    Each result: (path, filename, snippet, rank) — lower rank = better match."""
    q = query.strip()
    if not q:
        return []

    try:
        like = f'%{q}%'
        rows = conn.execute('''
            SELECT path, filename,
                   CASE
                       WHEN filename LIKE ? THEN 1
                       WHEN filename LIKE ? THEN 2
                       WHEN text IS NOT NULL AND text LIKE ? THEN 3
                       ELSE 4
                   END AS rank,
                   COALESCE(text, '') AS fulltext
            FROM files
            WHERE filename LIKE ?
               OR (text IS NOT NULL AND text LIKE ?)
            ORDER BY rank, filename
            LIMIT ?
        ''', (f'{q}%', like, like, like, like, limit)).fetchall()
    except Exception:
        applog.log_exc(f'DB: search failed for query {q!r}')
        return []

    results = []
    for path, filename, rank, fulltext in rows:
        snippet = _make_snippet(fulltext, q)
        results.append((path, filename, snippet, rank))
    return results


def _make_snippet(text, query, context=80):
    """Extract a snippet of text around the first occurrence of query.
    Returns empty string when text is empty or query isn't found in text."""
    if not text or not query:
        return ''
    idx = text.lower().find(query.lower())
    if idx == -1:
        return ''
    start = max(0, idx - context // 2)
    end = min(len(text), idx + len(query) + context // 2)
    snip = text[start:end].replace('\n', ' ').strip()
    if start > 0:
        snip = '…' + snip
    if end < len(text):
        snip = snip + '…'
    return snip
