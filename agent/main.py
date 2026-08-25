from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import random
import sys
import tkinter as tk
from tkinter import messagebox
from typing import Any

from .account_registry import AccountRegistry
from .browser_manager import BrowserManager
from .config import load_settings
from .laogu_api import LaoguApi
from .logger import build_logger
from .models import Task
from .task_manager import TaskManager
# 导入合规解封引擎与异常处理
from .x_automation_engine import WebE2ETestEngine, AutomationEngineError, SecurityVerificationRequired

PROGRESS_FILE = "daily_progress.json"
file_lock = asyncio.Lock()  # 协程锁，防止多窗口并发读写冲突


# ==================== 桌面弹窗提醒工具函数 ====================

def show_desktop_alert(title: str, message: str) -> None:
    """弹出桌面置顶提示框（阻塞/置顶强提醒）"""
    try:
        root = tk.Tk()
        root.withdraw()  # 隐藏 tkinter 主窗口
        root.attributes("-topmost", True)  # 强制置顶显示
        messagebox.showwarning(title, message)
        root.destroy()
    except Exception as e:
        print(f"桌面弹窗提醒显示失败: {e}")


# ==================== 多账号进度持久化存储逻辑 ====================

async def get_today_progress(profile_name: str) -> int:
    """安全读取指定 Profile 今日已完成的计数（按日期 + Profile 隔离）"""
    async with file_lock:
        today_str = datetime.date.today().isoformat()
        if not os.path.exists(PROGRESS_FILE):
            return 0
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(today_str, {}).get(profile_name, 0)
        except Exception:
            return 0


async def save_today_progress(profile_name: str, count: int) -> None:
    """安全保存/更新指定 Profile 今日已完成的计数"""
    async with file_lock:
        today_str = datetime.date.today().isoformat()
        data = {}
        if os.path.exists(PROGRESS_FILE):
            try:
                with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        if today_str not in data:
            data[today_str] = {}

        data[today_str][profile_name] = count

        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _print_accounts(records) -> None:
    headers = (
        "Profile",
        "Profile ID",
        "Browser",
        "Login",
        "X Username",
        "X Account ID",
        "Account Status",
        "Last Checked",
    )
    rows = [
        (
            item.profile_name or "-",
            item.profile_id,
            item.browser_status.value,
            item.login_status.value,
            item.x_username or "-",
            item.x_account_id or "-",
            item.account_status.value,
            item.last_checked.isoformat(),
        )
        for item in records
    ]
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    for row in [headers, *rows]:
        print(
            "  ".join(
                str(value).ljust(widths[index]) for index, value in enumerate(row)
            )
        )


def _run_account_registry_command(arguments: list[str]) -> int | None:
    if not arguments or arguments[0] not in {"accounts", "account"}:
        return None
    settings = load_settings()
    registry = AccountRegistry(
        settings.account_registry_file,
        settings.account_mapping_history_file,
    )
    if arguments[0] == "accounts":
        _print_accounts(registry.list())
        return 0
    if len(arguments) < 2 or not arguments[1].strip():
        print("PROFILE_ID is required", file=sys.stderr)
        return 2
    record = registry.get(arguments[1])
    if record is None:
        print(f"Account mapping not found: {arguments[1]}", file=sys.stderr)
        return 1
    _print_accounts([record])
    return 0


def _profile_by_name(profiles: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in profiles if str(item.get("profileName")) == name), None)


def _select_profiles(profiles: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], str]:
    first = _profile_by_name(profiles, "11")
    if first is None:
        raise RuntimeError("Required Profile 11 does not exist")

    second = _profile_by_name(profiles, "12")
    note = "Profile 12 found"
    if second is None:
        candidates = [
            item
            for item in profiles
            if item.get("profileId") != first.get("profileId")
            and str(item.get("profileName")) not in {"", "default"}
        ]
        if not candidates:
            raise RuntimeError("Profile 12 is absent and no second real Profile is available")
        second = candidates[0]
        note = f"Profile 12 absent; using discovered Profile {second.get('profileName')}"
    return first, second, note


# ==================== 时间段配额与自动化执行调度核心 ====================

def calculate_stage_target(total_daily_limit: int) -> int:
    """根据当前时刻动态计算 08-12 / 12-17 / 17-23 时间段应完成的累计指标 (33%:33%:34%)"""
    hour = datetime.datetime.now().hour
    if 8 <= hour < 12:
        return int(total_daily_limit * 0.33)
    elif 12 <= hour < 17:
        return int(total_daily_limit * 0.66)
    elif 17 <= hour < 23:
        return total_daily_limit
    else:
        return 0  # 23:00 - 08:00 夜间完全休眠


async def execute_x_automation_workflow(
    browser_manager: BrowserManager,
    profile: dict[str, Any],
    control_config: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, Any]:
    """
    单账户核心调度逻辑：自动对接解封配置与底层物理拟人自动化引擎，异常时桌面弹窗，进度本地持久化
    """
    profile_id = str(profile["profileId"])
    profile_name = str(profile.get("profileName", "默认"))

    try:
        # 1. 读取该 Profile 今日在本地文件中的实际已完成进度
        completed_today = await get_today_progress(profile_name)

        # 2. 连接/启动指纹浏览器并获取 CDP WebSocket 地址
        browser_info = browser_manager.start_browser(profile_id)
        cdp_url = browser_info.get("ws_url") or browser_info.get("cdp_url") or browser_info.get("cdpUrl")

        # 保底逻辑：如果 API 没有返回完整 URL，自动拼接你本地的 19876 端口
        if not cdp_url:
            port = browser_info.get("port") or 19876
            cdp_url = f"http://127.0.0.1:{port}"
            logger.info(f"[{profile_name}] 采用自动探测保底 CDP 地址: {cdp_url}")

        # 3. 动态拼接与获取 selectors_config.json 真实配置文件路径
        config_file_path = os.path.join(os.path.dirname(__file__), "selectors_config.json")

        # 4. 实例化解封引擎 (装载 CDP 地址及配置文件)
        engine = WebE2ETestEngine(
            cdp_url=cdp_url,
            config_path=config_file_path,
            logger=logger
        )

        daily_limit = control_config.get("daily_task_limit", 100)
        keyword = control_config.get("keyword", "")

        stage_target = calculate_stage_target(daily_limit)

        # A. 夜间休息时间段
        if stage_target == 0:
            logger.info(f"[{profile_name}] 当前处于夜间休息时段 (23:00 - 08:00)，进入休眠...")
            return {"status": "NIGHT_SLEEP", "profile_name": profile_name}

        # B. 当前节点目标未完成 -> 运行“任务模式”
        if completed_today < stage_target:
            needed = stage_target - completed_today
            batch_limit = min(needed, random.randint(5, 8))

            logger.info(f"[{profile_name}] 启动任务模式 | 当前时间段目标: {stage_target} | 今日已完成: {completed_today} | 本批次上限: {batch_limit}")
            
            # 使用 Playwright 连接 CDP 并运行真实物理操作流程
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()

                # 执行物理任务 (dry_run=False，允许执行真点击)
                result = await engine.execute_task(page, keyword=keyword, dry_run=False)

            # 捕获人机验证 / 风控拦截状态并弹窗提醒
            if result.get("status") == "PAUSED_FOR_USER_INPUT":
                alert_msg = f"🚨 窗口 [{profile_name}] 检测到人机验证 (Cloudflare/Captcha) 或风控拦截！\n\n自动化已强制暂停，请前往浏览器手动完成验证！"
                logger.error(f"[{profile_name}] 触发人机验证风控，弹窗提醒操作员！")
                show_desktop_alert(f"🛑 人机验证拦截警告 - {profile_name}", alert_msg)
                return result

            # 更新已完成数并写入本地 JSON 文件
            add_count = result.get("actions", 0)
            new_total = completed_today + add_count
            await save_today_progress(profile_name, new_total)

            logger.info(f"[{profile_name}] 本批次完成新增 {add_count}，今日累计写入文件: {new_total}")
            return result

        # C. 当前节点目标已提前达成 -> 切入“闲逛养号模式”
        else:
            logger.info(f"[{profile_name}] 节点目标已达成 ({completed_today}/{stage_target})，切入闲逛养号模式...")
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()

                # 开启 dry_run=True 纯观看浏览模式不触发点击，只平滑滚动
                result = await engine.execute_task(page, keyword=keyword, dry_run=True)

            return {"status": "IDLE_FINISHED", "profile_name": profile_name, "actions": 0}

    except Exception as exc:
        err_msg = f"❌ 窗口 [{profile_name}] 运行发生严重错误/异常！\n\n错误信息: {exc}"
        logger.error(f"[{profile_name}] 运行异常: {exc}", exc_info=True)
        # 遇到未知异常/报错时弹出桌面强提醒
        show_desktop_alert(f"⚠️ 脚本运行异常 - {profile_name}", err_msg)
        return {"status": "ERROR", "profile_name": profile_name, "error": str(exc)}


def main() -> int:
    registry_result = _run_account_registry_command(sys.argv[1:])
    if registry_result is not None:
        return registry_result

    parser = argparse.ArgumentParser(description="X 自动化控制中心多窗口调度入口")
    parser.add_argument("--keyword", type=str, default="", help="目标搜索关键词")
    parser.add_argument("--daily-limit", type=int, default=100, help="今日总目标关注/点赞量")
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    settings = load_settings()
    timeout_seconds = args.timeout or settings.default_timeout_seconds
    logger = build_logger(settings.log_file)
    api = LaoguApi(settings)
    browser_manager = BrowserManager(api)

    api.health()
    profiles = browser_manager.get_profiles()
    first, second, selection_note = _select_profiles(profiles)

    # 包含物理点击解封开关与测试参数配置
    control_config = {
        "keyword": args.keyword,
        "daily_task_limit": args.daily_limit,
        "max_follower_threshold": 300,
        "allow_interactions": True,  # 解封物理点击操作
        "dry_run": False,            # 执行真实的鼠标平滑移动与按压
    }

    report: dict[str, Any] = {
        "selection": {
            "note": selection_note,
            "first": {"profileId": first["profileId"], "profileName": first["profileName"]},
            "second": {"profileId": second["profileId"], "profileName": second["profileName"]},
        },
        "control_config": control_config,
        "results": {},
    }

    loop = asyncio.get_event_loop()
    try:
        task_11 = execute_x_automation_workflow(browser_manager, first, control_config, logger)
        task_12 = execute_x_automation_workflow(browser_manager, second, control_config, logger)

        results = loop.run_until_complete(asyncio.gather(task_11, task_12, return_exceptions=True))

        report["results"]["profile_11"] = str(results[0])
        report["results"]["profile_12"] = str(results[1])

    except Exception as exc:
        logger.error(f"调度执行异常: {exc}", exc_info=True)
        report["error"] = str(exc)

    settings.result_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())