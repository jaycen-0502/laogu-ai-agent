from pathlib import Path


project = Path(SPECPATH).parent.parent

a = Analysis(
    [str(project / "packaging" / "windows" / "laogu_desktop_entry.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[
        (str(project / "scripts" / "discover_x_account.js"), "scripts"),
        (str(project / "scripts" / "x_readonly_tasks.js"), "scripts"),
    ],
    hiddenimports=[
        # Loaded with importlib by agent.script_updater immediately before a
        # configured automation run. PyInstaller cannot discover that dynamic
        # import without an explicit entry.
        "agent.x_automation_engine",
        "playwright.async_api",
        "ntsecuritycon",
        "websocket",
        "win32api",
        "win32crypt",
        "win32security",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "alembic", "sqlalchemy", "fastapi", "uvicorn"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Laogu-Desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Laogu-Desktop",
)
