"""Dockie entry point.
Dispatches between the two roles the single packaged Dockie.exe serves:
the backend (default) and the on-demand search overlay (--ui), which the
backend spawns as a second instance of the same executable.
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
