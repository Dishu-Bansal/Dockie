"""
Updater.py — self-update for the Dockie desktop app.

On startup this module checks the hardcoded GitHub repository's latest
release. If the release version is newer than the bundled VERSION, it
downloads the new installer (dockie_setup_<version>.exe, built with Inno
Setup) from the release, launches it in a separate process, and exits
immediately so the running executable releases its file lock and the
installer can replace it automatically.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

# ── Hardcoded configuration ──
GITHUB_REPO = 'Dishu-Bansal/FileFinder'
# Keep in sync with the release tag: tag 'v1.0.0' <-> VERSION '1.0.0'.
VERSION = '1.0.2'
# Release assets are Inno Setup installers named dockie_setup_<version>.exe.
RELEASE_ASSET_PREFIX = 'dockie_setup_'
RELEASES_LATEST_URL = (
    f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
)
_USER_AGENT = 'Dockie-Updater'

# Windows process-creation flags used to launch the installer invisibly.
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008


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


def _asset_name(tag):
    """'v1.0.1' -> 'dockie_setup_v101.exe' (matches the .iss OutputBaseFilename)."""
    version = ''.join(str(p) for p in _version_tuple(tag))
    return f'{RELEASE_ASSET_PREFIX}v{version}.exe'


def _find_asset(release, name):
    for asset in release.get('assets') or []:
        if asset.get('name') == name:
            return asset
    return None


def _download(url, dest):
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, 'wb') as out:
        shutil.copyfileobj(resp, out)


def _download_path(asset_name):
    return os.path.join(tempfile.gettempdir(), asset_name)


def _run_and_exit(path):
    """Launch the installer in a new, detached, fully silent process, then
    kill this process so the running executable releases its file lock and
    the installer can replace it.

    /VERYSILENT + /SUPPRESSMSGBOXES make the Inno wizard run with no
    prompts (no "Create a desktop shortcut?" page, no questions); /NORESTART
    stops it from rebooting; /SP- skips the "This will install... continue?"
    prompt. Without these flags an auto-update would pop the interactive
    wizard on the user's screen."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    silent_flags = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-']
    subprocess.Popen(
        [path] + silent_flags,
        creationflags=_CREATE_NO_WINDOW | _DETACHED_PROCESS,
        startupinfo=startupinfo,
        close_fds=True,
    )
    sys.stdout.flush()
    os._exit(0)


def check_and_update():
    """Run the update check at app startup.

    Returns True if an update was applied (the process is exiting or about
    to exit), False if the app should keep starting normally.
    """
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

    asset_name = _asset_name(latest)
    asset = _find_asset(release, asset_name)
    if asset is None:
        print(f'[updater] Release {latest} has no {asset_name} asset')
        return False

    dest = _download_path(asset_name)
    try:
        # Remove any stale/partial download from a previous attempt.
        if os.path.exists(dest):
            os.remove(dest)
        print(f'[updater] Downloading {asset_name}...')
        _download(asset['browser_download_url'], dest)
    except OSError as e:
        print(f'[updater] Download failed: {e}')
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        return False

    print(f'[updater] Update {latest} ready; launching installer...')
    _run_and_exit(dest)
    return True  # unreachable; _run_and_exit never returns


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
        print(f'[updater] Expected asset: {_asset_name(tag)}')
