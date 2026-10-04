try:
    from .app import main
except ImportError as exc:
    if exc.name and exc.name.startswith("PySide6"):
        raise SystemExit("PySide6 未安装，请运行: python -m pip install -r desktop/requirements.txt")
    raise


exit_code = 0
try:
    exit_code = main()
except SystemExit as exc:
    exit_code = exc.code if isinstance(exc.code, int) else 0
except Exception:
    exit_code = 1
finally:
    import os
    os._exit(exit_code if isinstance(exit_code, int) else 0)
