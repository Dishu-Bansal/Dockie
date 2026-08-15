"""PDF filesystem scanner — finds all PDFs with aggressive directory pruning."""

import os

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


def find_pdfs(cancel_event=None):
    """Generator that yields PDF paths from all drives.
    Optionally accepts a threading.Event to cancel mid-scan."""
    roots = get_available_roots()
    user_skip_set = _build_user_skip_set()

    for root in roots:
        drive = root.rstrip('\\/')
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
                    if fname.lower().endswith('.pdf'):
                        yield os.path.join(dirpath, fname)
        except PermissionError:
            continue
