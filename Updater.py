"""
Updater.py — self-update for the Dockie desktop app.

On startup this module checks the hardcoded GitHub repository's latest
release. If the release version is newer than the bundled VERSION, it
downloads the release binary next to the currently running executable,
launches the new binary in a separate hidden process, and exits immediately
so the old executable stops locking its file on disk.

When the downloaded binary starts it takes over the canonical executable
name (Dockie.exe) once the previous process has released it.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ── Hardcoded configuration ──
GITHUB_REPO = 'Dishu-Bansal/FileFinder'
# Keep in sync with the release tag: tag 'v1.0.0' <-> VERSION '1.0.0'.
VERSION = '1.0.0'
RELEASE_ASSET = 'Dockie.exe'
RELEASES_LATEST_URL = (
    f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
)
_USER_AGENT = 'Dockie-Updater'

# Windows process-creation flags used to launch the new binary invisibly.
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008

_UPDATE_SUFFIX = '.new'


def _version_tuple(version):
    """'v1.2.3' -> (1, 2, 3); non-numeric junk is ignored."""
    return tuple(int(p) for p in re.findall(r'\d+', version or ''))


def _is_newer(latest_version, current_version):
    return _version_tuple(latest_version) > _version_tuple(current_version)


def _get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _latest_release():
    """Fetch the latest GitHub release, or None on any failure."""
    try:
        data = _get_json(RELEASES_LATEST_URL)
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or 'tag_name' not in data:
        return None
    return data


def _find_asset(release, name):
    for asset in release.get('assets') or []:
        if asset.get('name') == name:
            return asset
    return None


def _download(url, dest):
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, 'wb') as out:
        shutil.copyfileobj(resp, out)


def _update_path():
    """Downloaded binary lands next to the running exe as '<name>.new'."""
    return os.path.abspath(sys.executable) + _UPDATE_SUFFIX


def _spawn_and_exit(path):
    """Launch the new binary in a hidden, detached process, then kill this
    process so the old executable stops locking its file on disk."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    subprocess.Popen(
        [path],
        creationflags=_CREATE_NO_WINDOW | _DETACHED_PROCESS,
        startupinfo=startupinfo,
        close_fds=True,
    )
    sys.stdout.flush()
    os._exit(0)


def _take_over_previous():
    """If we are running from a '<name>.new' file, move ourselves over the
    canonical executable name once the previous process has released it."""
    if not getattr(sys, 'frozen', False):
        return
    exe = os.path.abspath(sys.executable)
    if not exe.lower().endswith(_UPDATE_SUFFIX):
        return
    canonical = exe[: -len(_UPDATE_SUFFIX)]
    for _ in range(100):  # wait up to ~10s for the old process to exit
        try:
            os.replace(exe, canonical)
            print(f'[updater] Installed update at {canonical}')
            return
        except OSError:
            time.sleep(0.1)


def check_and_update():
    """Run the update check at app startup.

    Returns True if an update was applied (the process is exiting or about
    to exit), False if the app should keep starting normally.
    """
    _take_over_previous()

    if not getattr(sys, 'frozen', False):
        print(f'[updater] Running from source; update check skipped (v{VERSION})')
        return False

    release = _latest_release()
    if release is None:
        print('[updater] Could not reach GitHub; staying on current version')
        return False

    latest = release['tag_name']
    if not _is_newer(latest, VERSION):
        print(f'[updater] Already up to date (v{VERSION})')
        return False

    asset = _find_asset(release, RELEASE_ASSET)
    if asset is None:
        print(f'[updater] Release {latest} has no {RELEASE_ASSET} asset')
        return False

    dest = _update_path()
    try:
        # Remove any stale/partial download from a previous attempt.
        if os.path.exists(dest):
            os.remove(dest)
        print(f'[updater] Downloading {RELEASE_ASSET} {latest}...')
        _download(asset['browser_download_url'], dest)
    except OSError as e:
        print(f'[updater] Download failed: {e}')
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        return False

    print(f'[updater] Update {latest} ready; relaunching...')
    _spawn_and_exit(dest)
    return True  # unreachable; _spawn_and_exit never returns


if __name__ == '__main__':
    print(f'[updater] Current version: {VERSION}')
    release = _latest_release()
    if release is None:
        print('[updater] Could not fetch latest release')
    else:
        tag = release['tag_name']
        newer = _is_newer(tag, VERSION)
        print(f'[updater] Latest release: {tag} '
              f'({"newer than current" if newer else "up to date"})')
