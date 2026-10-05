# -*- coding: utf-8 -*-
"""
Full packaging script for Laogu Control Center v0.21.94.
Key Features in v0.21.94:
1. 界面与标题版本号显式直观展示：
   - 窗口标题栏与顶部 Header 同步显式呈现当前本地运行版本 (v0.21.94)
   - 便于用户随时识别当前运行端精准版本，升级前后状态一目了然
2. 在线升级与自动重启深度加固：
   - updater 采用 Windows 原生 CreateProcess 独立脱壳拉起 Laogu-Desktop.exe，注入 SW_SHOWNORMAL 保证界面正常弹出
   - 支持启动阶段对 updater.exe.new / updater.py.new 的多轮重试无感平滑覆盖
   - 增加完整的时间戳 updater.log 审计日志追溯，全流程异常捕获不静默吞错
3. 严格安全与纯净规范：
   - 严格剔除 README.txt 及任何机器绑定凭据
   - 进程安全隔离：严禁触碰正在执行自动化任务的任何旧版实例
"""
import os
import sys
import shutil
import hashlib
import zipfile
import subprocess
from pathlib import Path

VERSION = "0.21.94"
PROJECT_ROOT = Path(r"C:\Users\Administrator\Documents\ChatGPT\New project\laogu-ai-agent-license-fix")
DESKTOP = Path(r"C:\Users\Administrator\Desktop")
DIST_DIR = PROJECT_ROOT / "dist" / "Laogu-Desktop"
RELEASE_DIR = PROJECT_ROOT / "release" / f"laogu-agent-control-center-v{VERSION}-windows-x64-portable"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def prepare_standard_configurations(folder: Path):
    """
    官方标准纯净版：为目标目录部署完整的认证配置文件与初始数据模板。
    严格排除个人绑定的 machine-specific 凭证。
    """
    cfg_dir = folder / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    env_content = (
        "# 老谷控制中心云端服务配置\n"
        "LAOGU_SERVER_URL=https://api.jaycwl.org\n"
        "LAOGU_AGENT_ID=\n"
        "LAOGU_AGENT_TOKEN=\n"
        "LAOGU_AGENT_HEARTBEAT_INTERVAL=15\n"
        "LAOGU_LOG_LEVEL=INFO\n"
    )
    (cfg_dir / "laogu.env").write_text(env_content, encoding="utf-8")
    (cfg_dir / "laogu.env.example").write_text(env_content, encoding="utf-8")

    tg_content = (
        '{\n'
        '  "bot_token": "",\n'
        '  "chat_id": "",\n'
        '  "enabled": false,\n'
        '  "notify_on_start": true,\n'
        '  "notify_on_complete": true,\n'
        '  "notify_on_error": true,\n'
        '  "notify_on_stats": true,\n'
        '  "daily_report_time": "22:00"\n'
        '}\n'
    )
    (cfg_dir / "telegram_config.json").write_text(tg_content, encoding="utf-8")
    (folder / "telegram_config.json").write_text(tg_content, encoding="utf-8")

    # 2. agent_data 目录与纯净初始数据库 (49KB)
    agent_data_dir = folder / "agent_data"
    agent_data_dir.mkdir(parents=True, exist_ok=True)
    (agent_data_dir / "engine_cache").mkdir(parents=True, exist_ok=True)
    (folder / "logs").mkdir(parents=True, exist_ok=True)

    clean_db_src = PROJECT_ROOT / "config" / "agent_state_clean.db"
    target_db = agent_data_dir / "agent_state.db"
    if clean_db_src.exists():
        shutil.copy2(clean_db_src, target_db)

    # 3. 部署使用手册与操作教程 (注意: 严格排除 README.txt)
    manuals = [
        "README_QUICKSTART.html",
        "README_QUICKSTART.txt",
        "X_Auto_Agent_User_Manual.html",
        "新手大白话操作教程.html",
        "新手大白话操作教程.txt",
        "老谷控制台操作说明.html",
    ]
    for m in manuals:
        src = PROJECT_ROOT / m
        if not src.exists():
            src = DESKTOP / "Laogu-Control-Center-0.21.91" / m
        if src.exists():
            shutil.copy2(src, folder / m)

    # 4. 拷贝独立的更新器
    updater_exe_src = PROJECT_ROOT / "packaging" / "dist" / "updater.exe"
    if updater_exe_src.exists():
        shutil.copy2(updater_exe_src, folder / "updater.exe")
    updater_py_src = PROJECT_ROOT / "updater.py"
    if updater_py_src.exists():
        shutil.copy2(updater_py_src, folder / "updater.py")

    # 5. 严格剔除已经认证/机器绑定的私有凭证与历史池，以及不需要打包的临时说明
    forbidden = [
        "README.txt",
        "credentials.json",
        "offline_access.json",
        "runtime_config.json",
        "visited_history_pool.json",
        "visited_history_pool.json.lock",
        "history_pool.json",
        "history_pool.json.lock",
        "agent_state.db-wal",
        "agent_state.db-shm",
        "agent_state.db.bak",
        "Laogu-Desktop.exe.old",
    ]
    for f in forbidden:
        for p in list(folder.rglob(f)):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

def build_updater():
    print("=== Compiling PyInstaller standalone updater.exe ===")
    spec_path = PROJECT_ROOT / "updater.spec"
    dist_path = PROJECT_ROOT / "packaging" / "dist"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", str(dist_path),
        str(spec_path)
    ]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller updater build failed with code {res.returncode}")

def build_pyinstaller():
    print(f"=== Compiling PyInstaller executable for v{VERSION} ===")
    spec_path = PROJECT_ROOT / "packaging" / "windows" / "laogu-desktop.spec"
    work_path = PROJECT_ROOT / "build" / "laogu-desktop"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", str(PROJECT_ROOT / "dist"),
        "--workpath", str(work_path),
        str(spec_path)
    ]
    print("Running:", " ".join(cmd))
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller build failed with return code {res.returncode}")

    internal_runtime = DIST_DIR / "_internal"
    if internal_runtime.exists():
        for icu_dll in internal_runtime.glob("icu*.dll"):
            try:
                icu_dll.unlink()
                print(f"Removed incompatible ICU DLL: {icu_dll.name}")
            except Exception as e:
                print(f"Notice: Could not remove {icu_dll.name}: {e}")

def package(rebuild: bool = True):
    print(f"=== Packaging Laogu Control Center v{VERSION} (Standard Clean Release) ===")

    # 检查是否有锁住 dist/Laogu-Desktop 的临时进程，仅限制该路径，绝不杀死桌面运行的实例
    try:
        import psutil
        for p in psutil.process_iter(['name', 'exe']):
            try:
                exe = p.info.get('exe') or ""
                if exe and str(DIST_DIR).lower() in exe.lower():
                    p.kill()
                    print(f"Closed dist lock process {p.pid} ({exe})")
            except Exception:
                pass
    except Exception:
        pass

    # 编译 standalone updater
    build_updater()

    if rebuild or not (DIST_DIR / "Laogu-Desktop.exe").exists():
        build_pyinstaller()
    else:
        print("[Build Cache] Using existing compiled binaries from dist/Laogu-Desktop.")

    assert DIST_DIR.exists(), f"Dist directory not found at {DIST_DIR}!"
    assert (DIST_DIR / "Laogu-Desktop.exe").exists(), "Executable missing!"

    # 拷贝独立 updater.exe 到 dist 目录
    updater_exe_src = PROJECT_ROOT / "packaging" / "dist" / "updater.exe"
    if updater_exe_src.exists():
        shutil.copy2(updater_exe_src, DIST_DIR / "updater.exe")
    updater_py_src = PROJECT_ROOT / "updater.py"
    if updater_py_src.exists():
        shutil.copy2(updater_py_src, DIST_DIR / "updater.py")

    # 1. 准备标准 Release 目录
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR, ignore_errors=True)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    dest_app_dir = RELEASE_DIR / f"Laogu-Control-Center-{VERSION}"
    shutil.copytree(DIST_DIR, dest_app_dir)

    # 2. 为发布目录注入官方标准配置与模板文件
    prepare_standard_configurations(dest_app_dir)

    # 3. 创建全功能标准便携式 Zip 包 (含认证配置文件)
    zip_filename = f"laogu-agent-control-center-v{VERSION}-windows-x64-portable.zip"
    zip_path = RELEASE_DIR / zip_filename
    print(f"\nCreating standard portable zip: {zip_path} ...")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(dest_app_dir.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(dest_app_dir)
                zf.write(file, arcname)

    desktop_zip = DESKTOP / zip_filename
    desktop_zip.unlink(missing_ok=True)
    shutil.copy2(zip_path, desktop_zip)
    size_mb = desktop_zip.stat().st_size / (1024 * 1024)
    print(f"[Desktop Portable Zip] Created on Desktop: {desktop_zip.name} ({size_mb:.2f} MB)")

    # 4. 创建 OTA 在线升级专用包 (纯程序不含用户私有配置，用于后台发布和客户端自动下载热更新)
    ota_zip_filename = f"Laogu-Desktop-Update-{VERSION}.zip"
    ota_zip_path = PROJECT_ROOT / "release" / ota_zip_filename
    ota_zip_path.parent.mkdir(parents=True, exist_ok=True)
    ota_zip_path.unlink(missing_ok=True)
    print(f"\nCreating OTA upgrade zip: {ota_zip_path} ...")

    with zipfile.ZipFile(ota_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(DIST_DIR.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(DIST_DIR)
                zf.write(file, arcname)

    desktop_ota_zip = DESKTOP / ota_zip_filename
    desktop_ota_zip.unlink(missing_ok=True)
    shutil.copy2(ota_zip_path, desktop_ota_zip)
    ota_size_mb = desktop_ota_zip.stat().st_size / (1024 * 1024)
    ota_sha256 = sha256_file(desktop_ota_zip)
    print(f"[OTA Update Zip] Created on Desktop: {desktop_ota_zip.name} ({ota_size_mb:.2f} MB, SHA256: {ota_sha256})")

    # 5. 更新桌面上的新版本目录 (Laogu-Control-Center-0.21.94)
    current_desktop_dir = DESKTOP / f"Laogu-Control-Center-{VERSION}"
    if current_desktop_dir.exists():
        shutil.rmtree(current_desktop_dir, ignore_errors=True)
    current_desktop_dir.mkdir(parents=True, exist_ok=True)
    for src_file in dest_app_dir.glob("*"):
        dst = current_desktop_dir / src_file.name
        if src_file.is_file():
            shutil.copy2(src_file, dst)
        elif src_file.is_dir():
            shutil.copytree(src_file, dst, dirs_exist_ok=True)
    prepare_standard_configurations(current_desktop_dir)
    print(f"\n[Desktop App Dir] Created {current_desktop_dir.name} with standard configurations.")

    print("\n=== Packaging Complete ===")
    print(f"Version: v{VERSION}")
    print(f"Full Zip: {desktop_zip}")
    print(f"OTA Zip: {desktop_ota_zip}")
    print(f"OTA SHA256: {ota_sha256}")
    return ota_sha256, ota_size_mb

if __name__ == "__main__":
    package(rebuild=True)
