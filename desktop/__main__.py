try:
    from .app import main
except ImportError as exc:
    if exc.name and exc.name.startswith("PySide6"):
        raise SystemExit("PySide6 未安装，请运行: python -m pip install -r desktop/requirements.txt")
    raise


raise SystemExit(main())
