"""
Single-pass PDF discovery across all Windows drives.
Reports total count and time taken. Skips system directories aggressively.
"""

import os
import time

# ── Tier 1: Root-level directory names to skip on every drive ──
# These are system/OS dirs that can appear on ANY drive, not just C:
SYSTEM_ROOT_NAMES = {
    '$Recycle.Bin', 'System Volume Information', '$WINDOWS.~TMP',
    '$Windows.~WS', '$WinREAgent', 'Recovery', 'MSOCache',
    'Config.Msi', 'PerfLogs', 'boot', 'EFI',
}

# ── Tier 2: C:-specific full path prefixes (OS + installed apps, not on other drives) ──
SKIP_PATH_PREFIXES_C = [
    r'C:\Windows',
    r'C:\Windows.old',
    r'C:\WinNT',
    r'C:\Program Files',
    r'C:\Program Files (x86)',
    r'C:\ProgramData',
    r'C:\Documents and Settings',
]

# ── Tier 3: Directory names to prune during walk ──
PRUNED_DIR_NAMES = {
    # Dev dependencies
    'node_modules', '.venv', 'venv', '.env', 'vendor',
    'bower_components', '.yarn', '.pnpm-store',
    # Python
    '__pycache__', '.pytest_cache', '.mypy_cache', '.tox',
    '.nox', 'dist', 'build', 'eggs', '.eggs',
    # VCS
    '.git', '.svn', '.hg',
    # Package caches
    '.npm', '.cargo', '.gradle', '.m2', '.ivy2', '.sbt',
    '.nuget', '.rustup',
    # Build artifacts
    'target', 'obj', 'bin', 'Debug', 'Release', 'x64', 'x86',
    'Generated', 'out', '.next', '.nuxt',
    # General cache
    '.cache', 'cache', '.thumbnails', 'thumbnails',
    # Temp + logs
    'tmp', 'temp', 'logs', '.log',
    # Heavy dev/data dirs with near-zero PDF yield
    'Sdk',                    # Android SDK — 79K dirs, 3 PDFs
    'WUDownloadCache',        # Windows Update cache — 41K dirs, 0 PDFs
    'vcpkg',                  # C++ package manager — 3K dirs, 0 PDFs
    'Anaconda',               # Python distribution — 16K dirs, scattered docs
}

# ── Tier 4: User profile subdirs to skip (relative to C:\Users\<name>\) ──
SKIP_USER_SUBDIRS = [
    r'AppData\Local\Temp',
    r'AppData\Local\Microsoft',
    r'AppData\Local\Packages',
    r'AppData\Local\Programs',
    r'AppData\Local\MicrosoftEdge',
    r'AppData\Local\Google',
    r'AppData\Local\Mozilla',
    r'AppData\Local\pip',
    r'AppData\Local\pnpm',
    r'AppData\Local\Yarn',
    r'AppData\Local\NuGet',
    r'AppData\Local\Docker',
    r'AppData\Local\JetBrains',
    r'AppData\Local\cache',
    r'AppData\Roaming\npm',
    r'AppData\Roaming\Code',
    r'AppData\Roaming\JetBrains',
    r'AppData\Roaming\Docker',
    r'AppData\Roaming\Composer',
    r'AppData\Roaming\NuGet',
    r'AppData\LocalLow',
    r'.nuget',
    r'.m2',
    r'.gradle',
    r'.cargo',
    r'.rustup',
    r'.yarn',
]


def should_skip_root(dirpath: str, drive: str) -> bool:
    """Check if a directory path should be skipped entirely.
    drive: e.g. 'C:', 'D:' — used to check C:-specific prefixes.
    """
    dirpath_norm = os.path.normpath(dirpath)

    # ── Check C:-specific prefixes ──
    if drive == 'C:':
        for prefix in SKIP_PATH_PREFIXES_C:
            prefix_norm = os.path.normpath(prefix)
            if dirpath_norm == prefix_norm or dirpath_norm.startswith(prefix_norm + os.sep):
                return True

    # ── Check if this is a root-directory inside the drive (e.g. D:\$Recycle.Bin) ──
    # Only applies when dirpath is exactly X:\Something (one level below drive root)
    drive_norm = os.path.normpath(drive + '\\')
    parent = os.path.dirname(dirpath_norm)
    if parent == drive_norm or parent == drive_norm.rstrip(os.sep):
        dirname = os.path.basename(dirpath_norm)
        if dirname in SYSTEM_ROOT_NAMES:
            return True

    return False


def build_skip_set(users_base: str) -> set:
    """Pre-build a set of normalized paths to skip under user profiles."""
    skip_set = set()
    try:
        for entry in os.scandir(users_base):
            if entry.is_dir():
                for sub in SKIP_USER_SUBDIRS:
                    skip_path = os.path.normpath(os.path.join(entry.path, sub))
                    skip_set.add(skip_path)
    except PermissionError:
        pass
    return skip_set


def scan_drive(root_path: str, user_skip_set: set, out_file) -> tuple[int, list[str], int]:
    """Walk a drive root, count PDFs and collect paths.
    Writes each PDF path to out_file. Returns (pdf_count, sample_paths, dirs_visited).
    """
    count = 0
    dirs_visited = 0
    sample_paths: list[str] = []
    drive = root_path.rstrip('\\/')
    last_report = time.perf_counter()
    last_dir_count = 0

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        dirs_visited += 1

        # Progress report every 3 seconds
        now = time.perf_counter()
        if now - last_report >= 3:
            rate = (dirs_visited - last_dir_count) / (now - last_report)
            print(f'  [{dirs_visited:,} dirs, {count:,} PDFs, {rate:,.0f} dirs/s] {dirpath}', end='\r')
            last_report = now
            last_dir_count = dirs_visited

        # ── Tier 1+2: skip system root prefixes and drive-level system dirs ──
        if should_skip_root(dirpath, drive):
            dirnames.clear()
            continue

        # ── Tier 3: prune directory names ──
        dirnames[:] = [d for d in dirnames if d not in PRUNED_DIR_NAMES]

        # ── Tier 4: prune user profile subdirs ──
        dirpath_norm = os.path.normpath(dirpath)
        skip_now = False
        for skip_path in user_skip_set:
            if dirpath_norm == skip_path or dirpath_norm.startswith(skip_path + os.sep):
                dirnames.clear()
                skip_now = True
                break
        if skip_now:
            continue

        # ── Find PDFs ──
        pdfs_in_dir = [fname for fname in filenames if fname.lower().endswith('.pdf')]
        if pdfs_in_dir:
            out_file.write(f'\n[{dirpath}]\n')
            for fname in pdfs_in_dir:
                full_path = os.path.join(dirpath, fname)
                out_file.write(f'  {fname}\n')
                count += 1
                if len(sample_paths) < 20:
                    sample_paths.append(full_path)

    # Clear the progress line
    print(' ' * 80, end='\r')
    return count, sample_paths, dirs_visited


def get_available_roots() -> list[str]:
    """Return all available drive roots that exist."""
    roots = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        root = f'{letter}:\\'
        if os.path.exists(root):
            roots.append(root)
    return roots


def scan_all() -> None:
    """Main entry: scan all drives and report results."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, 'pdfs_found.txt')

    print('=' * 60)
    print('PDF Scanner — Discovering all PDFs on this system')
    print(f'Output file: {output_path}')
    print('=' * 60)

    roots = get_available_roots()
    print(f'\nDrives found: {", ".join(roots)}')

    # Pre-build user-skip set
    user_skip_set: set[str] = set()
    users_base = r'C:\Users'
    if os.path.exists(users_base):
        print('Building user-profile skip list...')
        user_skip_set = build_skip_set(users_base)
        print(f'  → {len(user_skip_set)} user subdirectories will be skipped')

    grand_total = 0
    total_dirs = 0
    all_samples: list[str] = []

    start_time = time.perf_counter()

    with open(output_path, 'w', encoding='utf-8') as out_file:
        out_file.write('PDF Files Found — Full List\n')
        out_file.write('=' * 60 + '\n')

        for root in roots:
            print(f'\n{"─" * 40}')
            print(f'Scanning {root} ...')
            out_file.write(f'\n{"─" * 40}\n')
            out_file.write(f'Drive: {root}\n')
            out_file.write(f'{"─" * 40}\n')

            t0 = time.perf_counter()
            count, samples, dirs = scan_drive(root, user_skip_set, out_file)
            elapsed = time.perf_counter() - t0
            grand_total += count
            total_dirs += dirs
            all_samples.extend(samples)
            print(f'  Dirs visited: {dirs:,}  |  PDFs found: {count:,}  |  Time: {elapsed:.1f}s')

        out_file.write(f'\n{"=" * 60}\n')
        out_file.write(f'Total PDF files found: {grand_total:,}\n')
        out_file.write(f'Total directories visited: {total_dirs:,}\n')

    total_elapsed = time.perf_counter() - start_time

    print(f'\n{"=" * 60}')
    print(f'TOTAL PDF FILES FOUND:  {grand_total:,}')
    print(f'TOTAL DIRS VISITED:     {total_dirs:,}')
    print(f'Time taken:             {total_elapsed:.1f} seconds')
    print(f'Output written to:      {output_path}')
    print(f'{"=" * 60}')

    if all_samples:
        print(f'\nSample files (first {min(20, len(all_samples))}):')
        for path in all_samples[:20]:
            print(f'  {path}')


if __name__ == '__main__':
    scan_all()
