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

# FTS5 virtual table backing content search: an external-content index over
# `files` (see https://sqlite.org/fts5.html#external_content_tables). The
# triggers below keep it in sync with insert/update/delete on `files`, and
# the special 'rebuild' command backfills it from the content table during
# migration.
_FTS_TABLE_SQL = '''
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    filename, text,
    content='files',
    content_rowid='rowid',
    tokenize='porter unicode61'
)
'''

_FTS_TRIGGER_SQL = [
    '''CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
        INSERT INTO files_fts(rowid, filename, text)
        VALUES (new.rowid, new.filename, COALESCE(new.text, ''));
    END''',
    '''CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
        INSERT INTO files_fts(files_fts, rowid, filename, text)
        VALUES ('delete', old.rowid, old.filename, COALESCE(old.text, ''));
    END''',
    '''CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
        INSERT INTO files_fts(files_fts, rowid, filename, text)
        VALUES ('delete', old.rowid, old.filename, COALESCE(old.text, ''));
        INSERT INTO files_fts(rowid, filename, text)
        VALUES (new.rowid, new.filename, COALESCE(new.text, ''));
    END''',
]


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
        conn.execute('CREATE INDEX IF NOT EXISTS idx_files_scanned ON files(scanned_at)')
        _migrate_fts(conn)
        conn.commit()
    except Exception:
        applog.log_exc('DB: failed to initialize schema')
        raise


def _migrate_fts(conn):
    """Create the FTS5 content index (once) and backfill it from `files`.

    PRAGMA user_version gates the migration so existing databases are
    upgraded in place. 'rebuild' repopulates the whole index from the
    content table — the triggers only cover rows written afterwards.
    """
    try:
        if conn.execute('PRAGMA user_version').fetchone()[0] >= 1:
            return
        conn.execute(_FTS_TABLE_SQL)
        for trigger in _FTS_TRIGGER_SQL:
            conn.execute(trigger)
        conn.execute("INSERT INTO files_fts(files_fts) VALUES ('rebuild')")
        # The old btree index on files(text) never helped — leading-wildcard
        # LIKE can't use it — and content search is FTS5's job now.
        conn.execute('DROP INDEX IF EXISTS idx_files_text')
        conn.execute('PRAGMA user_version = 1')
    except Exception:
        applog.log_exc('DB: FTS5 unavailable — content search falls back to LIKE')


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


# Content search drives off the FTS5 index; the LIKE version is only used
# when the DB predates FTS5 (files_fts missing, e.g. never migrated).
# Both rank cheaply first (rowid/ids only) and only then join back to
# `files` for the text of the final LIMIT rows — carrying every matched
# row's fulltext through the ranking sort is the dominant cost. FTS5
# snippets are computed separately (_FTS_SNIPPET_SQL) only for the rows
# that survive the LIMIT: snippet() reads the matched rows' text, so
# computing it for every match dominates the query.
_FTS_SEARCH_SQL = '''
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
'''

# Snippet around the match for the surviving content matches. Matched
# tokens (possibly stems, not the literal query) are wrapped in
# char(2)/char(3) markers so the UI can highlight exactly what matched.
_FTS_SNIPPET_SQL = '''
    SELECT f.path, snippet(files_fts, 1, char(2), char(3), ' … ', 12)
    FROM files_fts
    JOIN files f ON f.rowid = files_fts.rowid
    WHERE files_fts MATCH ?
      AND f.path IN (%s)
'''

_LIKE_SEARCH_SQL = '''
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
'''


def fts5_match(query):
    """Build a safe FTS5 MATCH expression from free-text input.

    Each whitespace-separated term is quoted (embedded quotes doubled) so
    FTS5 operators ('-', '*', ':', parentheses, ...) typed by the user are
    treated literally; terms are ANDed, which is FTS5's default connector
    for a space-separated list."""
    return ' '.join(f'"{term.replace(chr(34), chr(34) * 2)}"'
                    for term in query.split())


def _fts_enabled(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='files_fts'"
    ).fetchone()
    return row is not None


def _fetch_snippets(conn, match, paths):
    """FTS5 snippets for the given content-matched paths: {path: snippet}.

    snippet() re-tokenizes the matched rows' text, so it is restricted to
    the rows the literal-find snippet cannot explain (search() passes only
    the surviving rank-3 paths it failed on). Matched tokens are wrapped
    in \x02/\x03 markers."""
    if not paths:
        return {}
    marks = ','.join('?' for _ in paths)
    return dict(conn.execute(
        _FTS_SNIPPET_SQL % marks, (match, *paths)).fetchall())


def search(conn, query, limit=20):
    """Search indexed files by filename and content. Returns ranked results.
    Each result: (path, filename, snippet, rank) — lower rank = better match."""
    q = query.strip()
    if not q:
        return []

    prefix = f'{q}%'
    contains = f'%{q}%'
    match = fts5_match(q)
    try:
        if _fts_enabled(conn):
            fts_path = True
            rows = conn.execute(
                _FTS_SEARCH_SQL, (prefix, contains, match, limit)).fetchall()
            # Literal-find snippets are cheap; FTS5's snippet() has to
            # re-tokenize the matched row's text, so reserve it for rows
            # the literal find cannot explain (stems, multi-word AND,
            # filename-column matches).
            snips = {}
            need_fts = []
            for path, filename, fulltext, rank in rows:
                if rank == 3:
                    lit = _make_snippet(fulltext, q)
                    if lit:
                        snips[path] = lit
                    else:
                        need_fts.append(path)
            snips.update(_fetch_snippets(conn, match, need_fts))
        else:
            fts_path = False
            rows = conn.execute(
                _LIKE_SEARCH_SQL, (prefix, contains, contains,
                                   contains, contains, limit)).fetchall()
            snips = {}
    except Exception:
        applog.log_exc(f'DB: search failed for query {q!r}')
        return []

    results = []
    for path, filename, fulltext, rank in rows:
        snippet = (snips.get(path) or '') if fts_path and rank == 3 else (
            _make_snippet(fulltext, q) if rank <= 3 else '')
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
