"""Dockie entry point — delegates to backend."""
from backend import main

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import applog
        applog.log_exc('Fatal unhandled error')
        raise

