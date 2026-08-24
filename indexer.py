"""Dockie entry point.

Runs the backend by default. The packaged exe also accepts --ui to launch
the search overlay standalone (manual/debug use); the backend itself builds
the overlay in-process, so the hotkey path never spawns a second process.
"""

import sys


def main():
    if '--ui' in sys.argv:
        import ui
        # ui.py reads a DB path from argv[1]; strip our switch so the
        # overlay falls back to DOCKIE_DB_PATH / ~/.dockie.
        sys.argv = [a for a in sys.argv if a != '--ui']
        return ui.main()
    from backend import main as backend_main
    return backend_main()


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import applog
        applog.log_exc('Fatal unhandled error')
        raise
