"""Document filesystem scanner — finds supported documents with aggressive directory pruning.

Indexed types (see SUPPORTED_EXTENSIONS): PDF, Word (.docx/.doc),
Excel (.xlsx/.xls) and plain text (.txt).
"""

import os

import applog

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

# Every file type Dockie indexes. Extension match is case-insensitive
# (see is_supported_file): '.PDF', '.DocX', … all count.
#   .pdf          PDF (PyMuPDF)
#   .docx / .doc  Word (python-docx when installed, else stdlib OOXML
#                 fallback for .docx; legacy binary .doc needs python-docx)
#   .xlsx / .xls  Excel (openpyxl when installed, else stdlib OOXML
#                 fallback for .xlsx; legacy binary .xls needs xlrd)
#   .txt          plain text (stdlib, encoding-sniffed)
SUPPORTED_EXTENSIONS = frozenset({
    '.pdf',
    '.docx', '.doc',
    '.xlsx', '.xls',
    '.txt',
})


def is_supported_file(path):
    """True when `path` has an indexed file extension (case-insensitive)."""
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTENSIONS


def get_available_roots():
    roots = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        root = f'{letter}:\\'
        if os.path.exists(root):
            roots.append(root)
    return roots


def _build_user_skip_set():
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


def _should_skip_root(dirpath, drive):
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


def find_documents(cancel_event=None):
    """Generator that yields supported document paths from all drives.
    Optionally accepts a threading.Event to cancel mid-scan."""
    roots = get_available_roots()
    applog.log(f'Scanner: scanning {len(roots)} drive(s): {", ".join(roots)}')
    user_skip_set = _build_user_skip_set()

    for root in roots:
        drive = root.rstrip('\\/')
        applog.log(f'Scanner: walking {root}')
        walked = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                if cancel_event and cancel_event.is_set():
                    return
                if _should_skip_root(dirpath, drive):
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
                    if cancel_event and cancel_event.is_set():
                        return
                    if is_supported_file(fname):
                        walked += 1
                        yield os.path.join(dirpath, fname)
        except PermissionError:
            continue  # expected on protected/system dirs
        except Exception:
            applog.log_exc(f'Scanner: unexpected error on {root}')
            continue
        applog.log(f'Scanner: finished {root} ({walked:,} documents)')


def find_pdfs(cancel_event=None):
    """Backward-compat alias: PDF-only view over find_documents()."""
    for path in find_documents(cancel_event):
        if path.lower().endswith('.pdf'):
            yield path
