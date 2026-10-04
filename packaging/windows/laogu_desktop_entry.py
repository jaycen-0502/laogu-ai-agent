import os

from desktop.app import main


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 0
    except Exception:
        exit_code = 1
    finally:
        os._exit(exit_code if isinstance(exit_code, int) else 0)
