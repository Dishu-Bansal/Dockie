"""Database layer — SQLite storage for indexed files."""

import os
import sqlite3
import time

DB_DIR = os.path.join(os.path.expanduser('~'), '.filefinder')
DB_PATH = os.path.join(DB_DIR, 'index.db')


def get_conn():
    """Return a connection. Each thread must call this — SQLite connections are NOT thread-safe."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db(conn):
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
