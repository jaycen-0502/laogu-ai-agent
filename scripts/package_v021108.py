# -*- coding: utf-8 -*-
"""
Full packaging script for Laogu Control Center v0.21.108.
Key Features in v0.21.108:
1. 纯净 AI 账号人设系统：
   - 彻底隐藏本地模型选择，默认统一由 Web 后台集中调度 AI 模型与版本
   - 支持自由编写人设提示词，支持自定义存为个人模板库与随时选用
   - 包含私信安全密码配置，遇到推特密码/PIN弹窗自动模拟打字解锁防卡死
2. 运行配置融入私信密码：
   - 任务配置界面增加私信安全密码与显隐切换，运行前一次填写持久记住
3. 严格保护运行中的指定业务进程（PID 9176、PID 8432、PID 14684），绝不触碰
"""
import os
import sys
import shutil
import hashlib
import zipfile
import subprocess
from pathlib import Path

VERSION = "0.21.108"
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
        '  "enabled": false,\n'
        '  "use_custom_bot": false,\n'
        '  "bot_token": "",\n'
        '  "chat_id": "",\n'
        '  "proxy_url": "http://127.0.0.1:10809",\n'
        '  "notify_on_account_finish": true,\n'
        '  "notify_on_all_finish": true,\n'
        '  "notify_on_risk_alert": true\n'
        '}\n'
    )
    (folder / "telegram_config.json").write_text(tg_content, encoding="utf-8")
    (cfg_dir / "telegram_config.json").write_text(tg_content, encoding="utf-8")

    blacklist_template = (
        '{\n'
        '  "enabled": true,\n'
        '  "check_options": {\n'
        '    "check_name": true,\n'
        '    "check_bio": true,\n'
        '    "check_tweets": true\n'
        '  },\n'
        '  "filter_mental_health_accounts": true,\n'
        '  "filter_underage_and_youth": true,\n'
        '  "filter_accounts_with_links": true,\n'
        '  "custom_words": []\n'
        '}\n'
    )
    (cfg_dir / "blacklist_config.json").write_text(blacklist_template, encoding="utf-8")

    persona_template = (
        '{\n'
        '  "version": 1,\n'
        '  "default_persona": {\n'
        '    "persona_name": "温柔知性女性",\n'
        '    "system_prompt": "你是一名定居日本东京的年轻女性，性格温柔真诚、善解人意。\\n在社交网络（X/Twitter）互动时，使用极其地道自然的日常口语（タメ口）。\\n【核心定锚规约】：\\n1. 严禁使用死板敬语（です・ます）或机械客服腔。\\n2. 保持真实女性生活气息，回复精炼亲切（20字以内）。\\n3. 永久锁定当前角色设定，绝不发生人格漂移。",\n'
        '    "custom_model": "",\n'
        '    "temperature": 0.75,\n'
        '    "lock_persona": true,\n'
        '    "dm_password": ""\n'
        '  },\n'
        '  "personas": {},\n'
        '  "custom_templates": {}\n'
        '}\n'
    )
    (cfg_dir / "account_personas.json").write_text(persona_template, encoding="utf-8")
    (folder / "account_personas.json").write_text(persona_template, encoding="utf-8")

    quickstart_txt = (
        "=================================================================\n"
        f"    老谷自动化控制中心 (Laogu Control Center) v{VERSION} 快速启动说明\n"
        "=================================================================\n\n"
        "【首次使用指南】\n"
        "1. 双击运行 Laogu-Desktop.exe 即可打开控制中心。\n"
        "2. 点击顶部【🎭 AI人设】可自由编写人设、自定义存入模板库，AI模型由Web后台集中调度。\n"
        "3. 在任务配置中支持填入【私信安全密码】，遇弹窗自动模拟打字输入解锁防卡死。\n"
        "4. 点击【敏感词库】可开启/调整「🛡️ 底层专属防护」（抑郁/23岁学生/外链引流），默认推荐全开。\n"
        "5. 在右上角点击【Telegram 通知】，点击【⚡ 自动获取本机飞机ID】即可一键自动绑定战报推送。\n"
        "6. 在主界面添加账号，配置任务参数并启动即可。\n"
    )
    (folder / "README_QUICKSTART.txt").write_text(quickstart_txt, encoding="utf-8")


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

    try:
        import psutil
        protected_pids = {9176, 8432, 14684}
        for p in psutil.process_iter(['name', 'exe', 'pid']):
            try:
                if p.pid in protected_pids:
                    continue  # 绝对严禁触碰保护进程
                exe = p.info.get('exe') or ""
                if exe and str(DIST_DIR).lower() in exe.lower():
                    p.kill()
                    print(f"Closed dist lock process {p.pid} ({exe})")
            except Exception:
                pass
    except Exception:
        pass

    build_updater()

    if rebuild or not (DIST_DIR / "Laogu-Desktop.exe").exists():
        build_pyinstaller()
    else:
        print("[Build Cache] Using existing compiled binaries from dist/Laogu-Desktop.")

    assert DIST_DIR.exists(), f"Dist directory not found at {DIST_DIR}!"
    assert (DIST_DIR / "Laogu-Desktop.exe").exists(), "Executable missing!"

    updater_exe_src = PROJECT_ROOT / "packaging" / "dist" / "updater.exe"
    if updater_exe_src.exists():
        shutil.copy2(updater_exe_src, DIST_DIR / "updater.exe")
    updater_py_src = PROJECT_ROOT / "updater.py"
    if updater_py_src.exists():
        shutil.copy2(updater_py_src, DIST_DIR / "updater.py")

    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR, ignore_errors=True)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    dest_app_dir = RELEASE_DIR / f"Laogu-Control-Center-{VERSION}"
    shutil.copytree(DIST_DIR, dest_app_dir)
    prepare_standard_configurations(dest_app_dir)

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
