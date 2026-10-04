"""
Playwright CDP engine with Multi-Persona AI Reply, VPS Remote Config Sync,
Bookmark & Retweet Protection, Time-Window Scheduling, Multi-Keyword Rotation,
and Control Center Compatibility.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Any
from urllib.parse import quote
import urllib.request

from agent.cloud_dedup import (
    _sync_cloud_claim_target,
    _sync_cloud_confirm_target,
)
from agent.history_pool import PersistentHistoryPool
from agent.blacklist_filter import (
    DEFAULT_BLACKLIST_WORDS,
    DEFAULT_CHECK_OPTIONS,
    check_user_blacklist,
    clean_target_creators,
    parse_blacklist_input,
)
from agent.telegram_notifier import TelegramNotifier


# ==================== VPS 远程热更新 & AI 评论生成模块 ====================

# Optional protected HTTPS configuration endpoint. Keep blank when the Router
# credentials are supplied directly through environment variables.
REMOTE_CONFIG_URL = os.environ.get("ROUTER_CONFIG_URL", "").strip()


def fetch_remote_router_config() -> dict:
    """从 VPS 远程配置中心拉取最新 API Key、Base URL 与 Model 列表"""
    if REMOTE_CONFIG_URL:
        try:
            if not REMOTE_CONFIG_URL.lower().startswith("https://"):
                raise ValueError("ROUTER_CONFIG_URL must use HTTPS")
            req = urllib.request.Request(
                REMOTE_CONFIG_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    raw_text = response.read().decode("utf-8")
                    clean_json_str = raw_text.replace("\u201c", '"').replace("\u201d", '"')
                    data = json.loads(clean_json_str)
                    if isinstance(data, dict) and data.get("api_key") and data.get("base_url"):
                        return data
        except Exception as err:
            print(f"  └─ ⚠️ [远程配置中心] 暂未获取到云端最新配置/网络超时，无缝回退本地配置: {err}")

    # 🛡️ 本地保底配置
    return {
        "base_url": os.environ.get("ROUTER_BASE_URL", ""),
        "api_key": os.environ.get("ROUTER_API_KEY", ""),
        "models": ["gpt-5.6-terra", "gpt-5.6", "gpt-5.5", "gpt-5.4", "gpt-4o-mini"]
    }


# 内存评论去重缓存与多线程锁（保证 asyncio.to_thread 并发安全）
_RECENT_REPLIES_CACHE: list[str] = []
_CACHE_LOCK = threading.Lock()


def generate_ai_reply(
    tweet_text: str,
    custom_api_key: str = "",
    custom_base_url: str = "",
    model_candidates: list[str] | str | None = None
) -> str:
    """日本本地化 AI 深度分析与评论生成器（CoT思维链分析语境/地域方言/情绪后输出纯平语）"""
    global _RECENT_REPLIES_CACHE

    if not tweet_text or len(tweet_text.strip()) < 5:
        return "めっちゃ助かる"

    remote_cfg = fetch_remote_router_config()

    API_KEY = custom_api_key or remote_cfg.get("api_key") or os.environ.get("ROUTER_API_KEY", "")
    BASE_URL = custom_base_url or remote_cfg.get("base_url") or os.environ.get("ROUTER_BASE_URL", "")

    if isinstance(model_candidates, str) and model_candidates.strip():
        models = [model_candidates.strip()]
    elif isinstance(model_candidates, list) and model_candidates:
        models = [str(m).strip() for m in model_candidates if str(m).strip()]
    else:
        models = remote_cfg.get("models") or ["gpt-5.6-terra", "gpt-5.5", "gpt-5.4", "gpt-4o-mini"]

    # 💡 系统角色定义：日本本地 SNS 深度分析专家 (强行封锁敬语)[cite: 3, 4]
    system_prompt = (
        "You are an expert in Japanese social media nuance and local dialects (関東弁, 関西弁, 博多弁, etc.). "
        "Your role is to analyze a tweet's emotion, intent, and language style, and then reply in natural, casual Japanese (タメ口). "
        "CRITICAL RULE: NEVER use polite forms or honorifics like 'です', 'ます', 'ございます', or 'でしょうか'."
    )

    # 💡 引入思维链 (Chain of Thought)：先分析，后生成[cite: 3, 4]
    prompt = (
        "以下のツイートを深層分析し、最適で自然なタメ口リプライを作成してください。\n\n"
        "【分析ステップ】\n"
        "1. 言語・方言の特定：標準的なネット口語か、関西弁・博多弁などの地方の方言が含まれているか判断する。\n"
        "2. 感情・ニュアンスの特定：共感、驚き、情報共有、愚痴、喜びなど、投稿者の感情を読み取る。\n"
        "3. 返信の決定：敬語を100%排除し、相手の感情と方言のテンションに合わせた最も適したリアルなタメ口フレーズを考案する。\n\n"
        "【厳格な出力ルール】\n"
        "- 敬語（です・ます・ございます等）は完全禁止。\n"
        "- 文末の句点（。）は絶対禁止。\n"
        "- 20文字以内のスマホ手打ちな自然なフレーズ。\n"
        "- 余計な解説は出力せず、最終的な【返信テキストのみ】を直接出力すること。\n\n"
        f"対象ツイート：\"{tweet_text[:300]}\""
    )

    if API_KEY and not API_KEY.startswith("sk-你的"):
        endpoint = BASE_URL.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            if endpoint.endswith("/v1"):
                endpoint = f"{endpoint}/chat/completions"
            else:
                endpoint = f"{endpoint}/v1/chat/completions"

        for model in models:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 50,
                    "temperature": 0.85,
                }
                req = urllib.request.Request(
                    endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {API_KEY}",
                        "User-Agent": "OpenAI-Python/1.12.0 (Codex-CLI-Engine)",
                        "Accept": "application/json",
                        "Connection": "keep-alive"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        res_data = json.loads(response.read().decode("utf-8"))
                        choices = res_data.get("choices", [])
                        if choices:
                            choice = choices[0]
                            text = choice.get("content", "") if "content" in choice else choice.get("message", {}).get("content", "")
                            clean_text = text.strip().replace('"', '').replace('\n', ' ')

                            # 文本过滤：剔除句尾句号/感叹号及可能残留的敬语后缀[cite: 3, 4]
                            clean_text = re.sub(r'[。\.！!]+$', '', clean_text).strip()
                            clean_text = re.sub(r'です$', '', clean_text)
                            clean_text = re.sub(r'ます$', '', clean_text)

                            # 线程安全的去重逻辑[cite: 3, 4]
                            with _CACHE_LOCK:
                                if clean_text and clean_text not in _RECENT_REPLIES_CACHE:
                                    _RECENT_REPLIES_CACHE.append(clean_text)
                                    if len(_RECENT_REPLIES_CACHE) > 50:
                                        _RECENT_REPLIES_CACHE.pop(0)

                                    print(f"  └─ 🤖 [AI 分析后生成平语] 模型 [{model}]: \"{clean_text}\"")
                                    return clean_text
            except Exception as err:
                print(f"  └─ ⚠️ [AI 评论] 模型 [{model}] 调用异常，尝试备用模型: {err}")

    # 兜底纯平语短句库[cite: 3, 4]
    fallback_replies = [
        "それな", "めっちゃ分かる", "まじで助かる", "ほんとこれすぎる",
        "なるほどな", "ええなこれ", "神かよ", "ほんとそれ"
    ]
    return random.choice(fallback_replies)


class AutomationEngineError(RuntimeError):
    """Expected automation failure that should be reported to the task log."""


class RateLimitPause(AutomationEngineError):
    def __init__(self, seconds: int, reason: str = ""):
        self.seconds = seconds
        self.reason = reason
        msg = f"Rate limit detected ({reason}); pause for {seconds} seconds" if reason else f"Rate limit detected; pause for {seconds} seconds"
        super().__init__(msg)


class CaptchaChallengeDetected(AutomationEngineError):
    def __init__(self, url: str):
        super().__init__(f"检测到人机验证页面 (account/access): {url}")
        self.url = url


class NotLoggedInError(AutomationEngineError):
    def __init__(self, handle: str = ""):
        super().__init__(f"账号未登录 (目标 Handle: {handle or '未知'})，请先在浏览器中手动登录 X 账号！")


class AutomationStopped(AutomationEngineError):
    """Raised when the control center requests task cancellation."""
    pass


CONTROL_EXCEPTION_TYPES = (
    AutomationStopped,
    RateLimitPause,
    CaptchaChallengeDetected,
    NotLoggedInError,
)


@dataclass(frozen=True)
class AutomationConfig:
    keyword: str = ""
    daily_task_limit: int = 100
    daily_tasks_used: int = 0
    max_follower_threshold: int = 1000
    max_statuses_threshold: int = 10000
    max_engagement_threshold: int = 10000
    batch_interval_minutes: int = 15
    execution_preset: str = "safe"     # 运行策略预设: "safe" (安全防风控) 或 "turbo" (极速高通量)
    batch_jitter_enabled: bool = True  # 是否启用批次间隔随机抖动
    outreach_mode: str = "keyword"      # 拓客工作模式: "keyword" (关键词推文), "keyword_chain" (关键词种子·无限顺藤摸瓜), "network_hop" (纯私域关注列表)
    target_url: str = "https://x.com/home"
    account_tag: str = "默认"
    profile_visit_ratio: float = 0.80
    home_browse_ratio: float = 0.20
    ai_reply_ratio: float = 0.0        # 默认不开启 AI 评论回复（0.0 关闭，保留用户自定义开启）
    bookmark_ratio: float = 0.25       # 25% 概率执行保存书签
    retweet_ratio: float = 0.10        # 10% 偶发转推概率
    chain_hop_enabled: bool = False    # 默认不开启顺藤摸瓜关系网拓客（保留用户自定义开启）
    chain_hop_ratio: float = 0.70      # 顺藤摸瓜触发率 (70%)
    max_chain_depth: int = 2           # 顺藤摸瓜最大递归深度 (0代表无限制持续深挖直到达成配额，1~10代表限定层数)
    max_harvest_per_seed: int = 10     # 单个种子博主关注列表的采摘饱和上限（默认10人，范围5~15人，防图谱爬取风控）
    chain_hop_source: str = "followers"  # 顺藤摸瓜来源列表：followers (关注者/粉丝，默认) 或 following (正在关注)
    in_situ_rest_enabled: bool = True  # 是否启用原地自然休眠（策略A，仅在透视+深挖双开时生效）
    hesitation_skip_ratio: float = 0.20
    schedule_mode: str = "smart"
    filter_verified_accounts: bool = True
    filter_parody_accounts: bool = True
    filter_bot_accounts: bool = True
    single_batch_mode: bool = False
    cloud_dedup_enabled: bool = False
    cloud_dedup_server_url: str = "https://api.jaycwl.org"
    cloud_dedup_studio_token: str = ""
    cloud_dedup_device_name: str = ""
    cold_start_warmup_enabled: bool = True
    cold_start_warmup_min_seconds: int = 45
    cold_start_warmup_max_seconds: int = 60
    cold_start_warmup_duration_seconds: int = 60
    casual_consumption_enabled: bool = True
    enable_soft_landing: bool = True  # 单日限额软着陆：达到 85% 上限后自动平滑降速防风控
    # --- 真人度自学习与闭环特征强化配置 ---
    authenticity_learning_enabled: bool = True
    authenticity_min_score: int = 60
    # --- 扫博主粉丝模式 & 频控延时配置 ---
    target_creators: list[str] = field(default_factory=list)
    max_followers_per_target: int = 50
    action_min_delay: float = 4.0
    action_max_delay: float = 8.0
    allow_follow: bool = True
    allow_like: bool = True
    allow_reply: bool = False
    allow_bookmark: bool = False
    allow_retweet: bool = False
    # --- 动态全字段黑名单词库 (一票否决) ---
    blacklist_filter_enabled: bool = True           # 是否启用全字段黑名单/敏感词过滤（一键开关，默认开启）
    blacklist_words: list[str] = field(default_factory=list)
    check_options: dict[str, bool] = field(default_factory=dict)
    # --- 关注比例维护与智能取关配置 ---
    smart_unfollow_enabled: bool = False
    unfollow_threshold_days: int = 5
    smart_schedule_enabled: bool = True            # 是否开启智能人类作息保护（中午 12:00~14:00 与深夜 22:00~09:00 自动休眠防封，不开启时全时段持续拓客）
    periodic_search_refresh_enabled: bool = True   # 是否开启定时重新检索/刷新最新推文流（默认开启，捕获1~3分钟内最新推文）
    search_refresh_interval_minutes: int = 15      # 定时刷新间隔（分钟，默认 15 分钟，范围 1~60）
    # --- CDP 动态时区与语言覆盖防风控配置 ---
    cdp_timezone_override_enabled: bool = True     # 是否启用 CDP 动态时区与语言覆盖（消灭 IP 与时区地域冲突指纹，默认开启）
    cdp_override_timezone: str = "auto"            # 目标时区预设: "auto" (自动探测代理所在国时区), "Asia/Tokyo", "America/New_York" 等
    cdp_override_locale: str = ""                  # 目标语言/区域 (选填，留空时根据时区自动映射)
    cdp_geolocation_override_enabled: bool = True  # 当启用 CDP 动态覆盖时，是否同步对齐目标城市的真实 GPS 经纬度（默认开启）
    # --- 推特 GraphQL 业务风控自动熔断配置 ---
    graphql_risk_pause_enabled: bool = True        # 是否启用推特接口致命风控自动熔断（Code 226/399/326/64 自动暂停防封，默认开启）
    # --- 锦上添花：高阶拟人防封与最新流自愈配置 ---
    natural_roaming_enabled: bool = True           # 是否启用自然摸鱼与行为噪声注入（批次间/周期性访问首页推荐流闲逛 15~30 秒，打乱单一搜索指纹）
    pre_click_guard_enabled: bool = True           # 是否启用推文卡片前置指标快速质检（进主页前快速识别默认头像、纯数字乱码号及低质营销推文，毫秒级跳过）
    search_pagination_refresh_enabled: bool = True  # 是否启用最新流深滚翻页自愈机制（最新搜索流深滚达限或断流时自动平滑回顶刷新，解决加载变慢与空白）
    # --- 实验性功能：底层数据流极速透视预筛 ---
    graphql_scout_filter_enabled: bool = True       # 是否启用底层数据流极速透视预筛（拦截 SearchTimeline 响应内存秒级打分预筛，默认开启，完全自由选择关闭）
    smart_newbie_recognition_enabled: bool = True   # 是否启用真人新人智能识别（智能吸纳带系统随机数字但拥有正常头像与真实原创推文的新手小白，默认开启，自由选择关闭）
    block_video_streams: bool = True               # 极速省流防卡：是否底层阻断推特大体积视频流切片预加载（默认开启，释放60%内存与显存占用，防多开卡屏）
    dry_run: bool = False                           # 预演模式（只浏览不执行真实互动）
    follow_burst_cooling_enabled: bool = True       # 高频爆发降频冷却（连续关注达到阈值时自动拟人微歇）



    @property
    def is_chain_infinite(self) -> bool:
        return self.outreach_mode == "keyword_chain" or self.max_chain_depth == 0

    @property
    def bypass_time_window(self) -> bool:
        if not self.smart_schedule_enabled:
            return True
        return self.single_batch_mode

    def compute_batch_cooldown(self) -> tuple[int, int]:
        """计算批次间的休息分钟数与微扰动秒数。
        返回 (cooldown_minutes, micro_jitter_seconds)。
        """
        base_interval = max(1, self.batch_interval_minutes)
        if not self.batch_jitter_enabled:
            return base_interval, 0

        if self.execution_preset == "turbo":
            # 极速模式：微抖动，保证高频吞吐（如果用户配置 1 或 2 分钟，绝不强制长时等待）
            if base_interval <= 1:
                cooldown_min = 1
            elif base_interval == 2:
                cooldown_min = max(1, 2 + random.choice([-1, 0, 1]))
            else:
                cooldown_min = max(1, base_interval + random.randint(-1, 1))
            micro_jitter = random.randint(5, 25)
        else:
            # 安全模式：常规拟人随机浮动
            if base_interval <= 2:
                cooldown_min = max(1, base_interval + random.choice([0, 1]))
            elif base_interval <= 5:
                cooldown_min = max(1, base_interval + random.randint(-1, 2))
            else:
                cooldown_min = max(1, base_interval + random.randint(-2, 3))
            micro_jitter = random.randint(10, 35)

        return cooldown_min, micro_jitter

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "AutomationConfig":
        values = values or {}
        if isinstance(values.get("active"), dict):
            merged = dict(values.get("active") or {})
            merged.update({key: value for key, value in values.items() if key != "active"})
            values = merged

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(values.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        def ratio(name: str, default: float) -> float:
            value = values.get(name, default)
            if isinstance(value, bool):
                if name == "ai_reply_ratio":
                    return 0.15 if value else 0.0
                return default
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = default
            return max(0.0, min(1.0, parsed))

        def boolean(name: str, default: bool) -> bool:
            val = values.get(name, default)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() not in {"false", "0", "no"}
            return bool(val)

        kw = str(values.get("keyword") or values.get("keywords") or values.get("search_keyword") or "").strip()[:500]
        tag = str(values.get("account_tag") or values.get("profile_name") or values.get("profile_id") or "默认").strip()

        schedule_mode = str(values.get("schedule_mode") or "smart").strip().lower()
        if schedule_mode not in {"smart", "immediate", "scheduled"}:
            schedule_mode = "smart"

        if "smart_schedule_enabled" in values:
            smart_schedule_enabled = boolean("smart_schedule_enabled", True)
        elif schedule_mode in {"immediate", "scheduled"}:
            # 向后兼容单元测试：若显式指定了立即执行或自定义定时且未指定 smart_schedule_enabled，保持放行
            smart_schedule_enabled = False
        else:
            smart_schedule_enabled = True

        execution_preset = str(values.get("execution_preset") or values.get("speed_mode") or "safe").strip().lower()
        if execution_preset not in {"safe", "turbo"}:
            execution_preset = "safe"
        batch_jitter_enabled = boolean("batch_jitter_enabled", True)

        outreach_mode = str(values.get("outreach_mode") or "keyword").strip().lower()
        if outreach_mode not in {"keyword", "keyword_chain", "network_hop", "target_followers"}:
            outreach_mode = "keyword"

        server_url = str(values.get("cloud_dedup_server_url") or values.get("server_url") or "https://api.jaycwl.org").strip().rstrip("/")
        studio_token = str(values.get("cloud_dedup_studio_token") or values.get("studio_token") or "").strip()
        device_name = str(values.get("cloud_dedup_device_name") or values.get("device_name") or os.environ.get("COMPUTERNAME", "")).strip()
        if "cloud_dedup_enabled" in values:
            cloud_dedup_enabled = bool(values["cloud_dedup_enabled"])
        else:
            cloud_dedup_enabled = bool(studio_token)

        raw_creators = values.get("target_creators") or values.get("target_creators_list") or []
        target_creators = clean_target_creators(raw_creators)
        if not target_creators and outreach_mode == "target_followers" and kw:
            target_creators = clean_target_creators(kw)

        min_d = float(values.get("action_min_delay") or 4.0)
        max_d = float(values.get("action_max_delay") or 8.0)
        if min_d > max_d:
            min_d, max_d = max_d, min_d

        raw_bl = values.get("blacklist_words")
        blacklist_words = parse_blacklist_input(raw_bl) if raw_bl is not None else list(DEFAULT_BLACKLIST_WORDS)
        raw_opts = values.get("check_options")
        if isinstance(raw_opts, dict):
            check_opts = {
                "check_name": bool(raw_opts.get("check_name", True)),
                "check_bio": bool(raw_opts.get("check_bio", True)),
                "check_tweets": bool(raw_opts.get("check_tweets", True)),
            }
        else:
            check_opts = dict(DEFAULT_CHECK_OPTIONS)

        return cls(
            keyword=kw,
            daily_task_limit=integer("daily_task_limit", 100, 1, 10_000),
            daily_tasks_used=integer("daily_tasks_used", 0, 0, 10_000),
            max_follower_threshold=integer("max_follower_threshold", 1000, 0, 100_000_000),
            max_statuses_threshold=integer("max_statuses_threshold", 10_000, 0, 100_000_000),
            max_engagement_threshold=integer("max_engagement_threshold", 10_000, 0, 100_000_000),
            batch_interval_minutes=integer("batch_interval_minutes", 15, 1, 1440),
            execution_preset=execution_preset,
            batch_jitter_enabled=batch_jitter_enabled,
            outreach_mode=outreach_mode,
            target_url=str(values.get("target_url") or "https://x.com/home").strip()[:500],
            account_tag=tag,
            profile_visit_ratio=ratio("profile_visit_ratio", 0.80),
            home_browse_ratio=ratio("home_browse_ratio", 0.20),
            ai_reply_ratio=ratio("ai_reply_ratio", 0.0),
            bookmark_ratio=ratio("bookmark_ratio", 0.25),
            retweet_ratio=ratio("retweet_ratio", 0.10),
            chain_hop_enabled=boolean("chain_hop_enabled", False),
            chain_hop_ratio=ratio("chain_hop_ratio", 0.70),
            max_chain_depth=integer("max_chain_depth", 2, 0, 10),
            max_harvest_per_seed=integer("max_harvest_per_seed", 10, 1, 30),
            chain_hop_source=str(values.get("chain_hop_source") or "followers").strip().lower() if str(values.get("chain_hop_source") or "").strip().lower() in {"followers", "following"} else "followers",
            in_situ_rest_enabled=boolean("in_situ_rest_enabled", True),
            hesitation_skip_ratio=ratio("hesitation_skip_ratio", 0.20),
            schedule_mode=schedule_mode,
            filter_verified_accounts=boolean("filter_verified_accounts", True),
            filter_parody_accounts=boolean("filter_parody_accounts", True),
            filter_bot_accounts=boolean("filter_bot_accounts", True),
            single_batch_mode=boolean("single_batch_mode", False),
            cloud_dedup_enabled=cloud_dedup_enabled,
            cloud_dedup_server_url=server_url,
            cloud_dedup_studio_token=studio_token,
            cloud_dedup_device_name=device_name,
            cold_start_warmup_enabled=boolean("cold_start_warmup_enabled", True),
            cold_start_warmup_min_seconds=integer("cold_start_warmup_min_seconds", 45, 10, 300),
            cold_start_warmup_max_seconds=integer("cold_start_warmup_max_seconds", 60, 10, 300),
            cold_start_warmup_duration_seconds=integer("cold_start_warmup_duration_seconds", 60, 10, 300),
            casual_consumption_enabled=boolean("casual_consumption_enabled", True),
            enable_soft_landing=boolean("enable_soft_landing", values.get("soft_landing_enabled", True)),
            authenticity_learning_enabled=boolean("authenticity_learning_enabled", True),
            authenticity_min_score=integer("authenticity_min_score", 60, 0, 100),
            target_creators=target_creators,
            max_followers_per_target=integer("max_followers_per_target", 50, 1, 10_000),
            action_min_delay=min_d,
            action_max_delay=max_d,
            allow_follow=boolean("allow_follow", True),
            allow_like=boolean("allow_like", True),
            allow_reply=boolean("allow_reply", False),
            allow_bookmark=boolean("allow_bookmark", False),
            allow_retweet=boolean("allow_retweet", False),
            blacklist_filter_enabled=boolean("blacklist_filter_enabled", True),
            blacklist_words=blacklist_words,
            check_options=check_opts,
            smart_unfollow_enabled=boolean("smart_unfollow_enabled", False),
            unfollow_threshold_days=integer("unfollow_threshold_days", 5, 1, 90),
            smart_schedule_enabled=smart_schedule_enabled,
            periodic_search_refresh_enabled=boolean("periodic_search_refresh_enabled", True),
            search_refresh_interval_minutes=integer("search_refresh_interval_minutes", 15, 1, 60),
            cdp_timezone_override_enabled=boolean("cdp_timezone_override_enabled", True),
            cdp_override_timezone=str(values.get("cdp_override_timezone") or "auto").strip(),
            cdp_override_locale=str(values.get("cdp_override_locale") or "").strip(),
            cdp_geolocation_override_enabled=boolean("cdp_geolocation_override_enabled", True),
            graphql_risk_pause_enabled=boolean("graphql_risk_pause_enabled", True),
            natural_roaming_enabled=boolean("natural_roaming_enabled", True),
            pre_click_guard_enabled=boolean("pre_click_guard_enabled", True),
            search_pagination_refresh_enabled=boolean("search_pagination_refresh_enabled", True),
            graphql_scout_filter_enabled=boolean("graphql_scout_filter_enabled", True),
            smart_newbie_recognition_enabled=boolean("smart_newbie_recognition_enabled", True),
            block_video_streams=boolean("block_video_streams", True),
            dry_run=boolean("dry_run", False),
            follow_burst_cooling_enabled=boolean("follow_burst_cooling_enabled", True),
        )



class ProfilePersonality:
    """出海商务 & 美女 IP 多维专属性格基因库"""

    def __init__(self, cdp_url: str):
        port_match = re.search(r":(\d+)", cdp_url)
        self.port = port_match.group(1) if port_match else cdp_url

        hash_val = int(hashlib.md5(cdp_url.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(hash_val)

        personality_index = hash_val % 8

        if personality_index == 0:
            self.p_type = "外贸商务稳重型"
            self.mean_cool_down = rng.uniform(30.0, 48.0)
            self.mouse_steps = rng.randint(20, 35)
            self.press_duration = (rng.uniform(0.12, 0.25), rng.uniform(0.25, 0.40))
        elif personality_index == 1:
            self.p_type = "美女IP-精致时尚型"
            self.mean_cool_down = rng.uniform(16.0, 26.0)
            self.mouse_steps = rng.randint(10, 20)
            self.press_duration = (rng.uniform(0.06, 0.15), rng.uniform(0.15, 0.28))
        elif personality_index == 2:
            self.p_type = "美女IP-高情绪感互动型"
            self.mean_cool_down = rng.uniform(18.0, 30.0)
            self.mouse_steps = rng.randint(12, 22)
            self.press_duration = (rng.uniform(0.08, 0.18), rng.uniform(0.18, 0.30))
        elif personality_index == 3:
            self.p_type = "美女IP-随性日常闲逛型"
            self.mean_cool_down = rng.uniform(20.0, 35.0)
            self.mouse_steps = rng.randint(14, 26)
            self.press_duration = (rng.uniform(0.09, 0.19), rng.uniform(0.19, 0.32))
        elif personality_index == 4:
            self.p_type = "跨时区高效拓客型"
            self.mean_cool_down = rng.uniform(20.0, 32.0)
            self.mouse_steps = rng.randint(15, 25)
            self.press_duration = (rng.uniform(0.08, 0.18), rng.uniform(0.18, 0.30))
        elif personality_index == 5:
            self.p_type = "谨慎风控考察型"
            self.mean_cool_down = rng.uniform(40.0, 60.0)
            self.mouse_steps = rng.randint(30, 45)
            self.press_duration = (rng.uniform(0.15, 0.30), rng.uniform(0.30, 0.50))
        elif personality_index == 6:
            self.p_type = "美女IP-夜猫冲浪型"
            self.mean_cool_down = rng.uniform(15.0, 28.0)
            self.mouse_steps = rng.randint(10, 18)
            self.press_duration = (rng.uniform(0.07, 0.16), rng.uniform(0.16, 0.29))
        else:
            self.p_type = "深耕社群 KOL 拜访型"
            self.mean_cool_down = rng.uniform(25.0, 42.0)
            self.mouse_steps = rng.randint(18, 32)
            self.press_duration = (rng.uniform(0.10, 0.22), rng.uniform(0.22, 0.35))

        self.std_dev_cool_down = rng.uniform(4.0, 8.0)

    def get_cooldown(self) -> float:
        val = random.gauss(self.mean_cool_down, self.std_dev_cool_down)
        return max(15.0, min(90.0, val))


class AccountFilterGuard:
    """垃圾账号与黄推过滤器"""

    AD_KEYWORDS = [
        "discount", "promo", "coupon", "shop", "sale", "buy now",
        "微信", "加v", "联系方式", "下单", "优惠", "代购", "接单", "出号",
        "telegram", "tg:", "vx:", "客服", "官网", "点击链接", "商务合作",
        "副業", "副収入", "在宅ワーク", "自動化", "固定ツイート", "公式line", 
        "プロフリンク", "プロフ見て", "稼ぐ", "収益化", "ビジネス"
    ]

    NSFW_KEYWORDS = [
        "nsfw", "18+", "18岁", "黄推", "约", "福利", "门槛", "看文", "私信看",
        "出包", "自慰", "性感", "原神姬", "骚", "裸", "pussy", "nude", "sex",
        "onlyfans", "fanbox", "反差", "兼职", "空降", "同城",
        "裏垢", "裏アカ", "裏垢女子", "裏垢男子", "パパ活", "ママ活", "オフパコ", 
        "エロ", "マン凸", "パイ凸", "おふパコ", "セフレ", "18禁", "無修正",
        "サブ垢", "さぶあか", "sub垢", "え○ち", "えっち", "エッチ",
        "年上のおじさま", "大人的男性", "大人の男性", "年上の男性が好き", "甘えたい夜",
        "ヨシヨシしてほしい", "かまってほしい", "すぐ懐きます", "懐いちゃいます",
        "オナニー", "ファンティア", "myfans", "マイファンズ"
    ]

    PARODY_KEYWORDS = [
        "戏仿账号", "戏仿", "parody", "fan account", "fans account",
        "automated account", "自动化账号", "非公式", "非官方", "🎭",
        "fanpage", "fan page", "parody account", "ファンアカウント", "非公式アカウント"
    ]

    BOT_CJK_REGEX = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]bot|bot[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', re.IGNORECASE)
    BOT_BOUNDARY_REGEX = re.compile(r'(?:^|[^a-z0-9])bot(?:[^a-z0-9]|$)', re.IGNORECASE)
    BOT_PREFIX_REGEX = re.compile(r'^bot(?:[_\-.]|\d+)', re.IGNORECASE)
    BOT_SUFFIX_REGEX = re.compile(r'([a-z0-9]+)bot(\d*)$', re.IGNORECASE)
    BOT_KEYWORD_REGEX = re.compile(r'机器人|機器人|自动化|自動化|ロボット|\brobot\b', re.IGNORECASE)
    EXCLUDED_BOT_WORDS = {"abbott", "talbot", "turbot", "both", "bottom", "bottle", "bother", "sabotage"}

    @classmethod
    def is_spam_or_nsfw(cls, text: str) -> tuple[bool, str]:
        if not text:
            return False, ""
        text_lower = text.lower()

        # 罩杯特征过滤（如 Iカップ, Fカップ，避开 ワールドカップ, カップ麺）
        cup_match = re.search(r"(?<![a-z0-9])[a-z]カップ", text_lower)
        if cup_match:
            return True, f"色情/擦边罩杯账号 (特征: {cup_match.group(0).upper()})"

        for kw in cls.NSFW_KEYWORDS:
            if kw in text_lower:
                return True, f"色情/黄推/裏垢账号 (特征词: {kw})"

        for kw in cls.AD_KEYWORDS:
            if kw in text_lower:
                return True, f"广告/营销/副業账号 (特征词: {kw})"

        return False, ""

    @classmethod
    def is_parody_or_automated(cls, text: str) -> tuple[bool, str]:
        if not text:
            return False, ""
        text_lower = text.lower()
        for kw in cls.PARODY_KEYWORDS:
            if kw.lower() in text_lower:
                return True, f"戏仿/非官方/自动化账号 (特征: {kw})"
        return False, ""

    @classmethod
    def is_bot_name_or_handle(cls, name: str, handle: str) -> tuple[bool, str]:
        for candidate, kind in [(name, "昵称"), (handle, "账号ID")]:
            if not candidate:
                continue
            cand_clean = candidate.lower().strip('@').strip()
            if cls.BOT_CJK_REGEX.search(cand_clean):
                return True, f"{kind}包含bot (特征: {candidate})"
            if cls.BOT_BOUNDARY_REGEX.search(cand_clean):
                return True, f"{kind}包含bot (特征: {candidate})"
            if cls.BOT_PREFIX_REGEX.search(cand_clean):
                return True, f"{kind}以bot开头 (特征: {candidate})"
            match = cls.BOT_SUFFIX_REGEX.search(cand_clean)
            if match:
                full_matched = match.group(0)
                if full_matched not in cls.EXCLUDED_BOT_WORDS and match.group(1) not in {"al", "tal"}:
                    return True, f"{kind}包含bot后缀 (特征: {candidate})"
            if cls.BOT_KEYWORD_REGEX.search(cand_clean):
                return True, f"{kind}包含机器人特征 (特征: {candidate})"
        return False, ""

    @classmethod
    def check_candidate_account(
        cls,
        name: str = "",
        handle: str = "",
        card_or_dom_text: str = "",
        has_verified_badge: bool = False,
        config: Any = None,
    ) -> tuple[bool, str]:
        filter_verified = getattr(config, "filter_verified_accounts", True) if config else True
        if filter_verified and has_verified_badge:
            return True, "已认证账号 (蓝勾/金勾)"

        filter_parody = getattr(config, "filter_parody_accounts", True) if config else True
        if filter_parody:
            combined_text = f"{name} {handle} {card_or_dom_text}".strip()
            is_parody, p_reason = cls.is_parody_or_automated(combined_text)
            if is_parody:
                return True, p_reason

        filter_bot = getattr(config, "filter_bot_accounts", True) if config else True
        if filter_bot:
            is_bot, b_reason = cls.is_bot_name_or_handle(name, handle)
            if is_bot:
                return True, b_reason

        combined_text = f"{name} {handle} {card_or_dom_text}".strip()
        is_bad, bad_reason = cls.is_spam_or_nsfw(combined_text)
        if is_bad:
            return True, bad_reason

        # 动态全字段黑名单词库（一票否决制）
        bl_enabled = getattr(config, "blacklist_filter_enabled", True) if config else True
        bl_words = (getattr(config, "blacklist_words", None) or []) if bl_enabled else []
        if bl_words:
            check_opts = getattr(config, "check_options", None) or DEFAULT_CHECK_OPTIONS
            cand_user = {
                "display_name": name,
                "screen_name": handle,
                "bio": card_or_dom_text,
                "recent_tweets": getattr(config, "_candidate_recent_tweets", []),
            }
            is_bl, hit_word = check_user_blacklist(cand_user, bl_words, check_opts)
            if is_bl:
                return True, f"命中黑名单敏感词 (特征词: {hit_word})"

        return False, ""


def calculate_cubic_bezier_point(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], t: float) -> tuple[float, float]:
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t

    x = uuu * p0[0] + 3.0 * uu * t * p1[0] + 3.0 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3.0 * uu * t * p1[1] + 3.0 * u * tt * p2[1] + ttt * p3[1]
    return x, y


def generate_bezier_trajectory(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int,
    max_bounds: tuple[float, float] = (1280.0, 800.0),
) -> list[tuple[float, float]]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)

    if steps <= 1 or distance < 5:
        return [end]

    normal_x = -dy / distance
    normal_y = dx / distance
    curvature_mag = random.gauss(0, min(distance * 0.18, 120.0))

    ctrl1_x = start[0] + dx * random.uniform(0.2, 0.4) + normal_x * curvature_mag
    ctrl1_y = start[1] + dy * random.uniform(0.2, 0.4) + normal_y * curvature_mag

    ctrl2_x = start[0] + dx * random.uniform(0.6, 0.8) + normal_x * (curvature_mag * random.uniform(0.4, 0.8))
    ctrl2_y = start[1] + dy * random.uniform(0.6, 0.8) + normal_y * (curvature_mag * random.uniform(0.4, 0.8))

    max_w, max_h = max_bounds
    ctrl1_x = max(10.0, min(max_w - 10.0, ctrl1_x))
    ctrl1_y = max(10.0, min(max_h - 10.0, ctrl1_y))
    ctrl2_x = max(10.0, min(max_w - 10.0, ctrl2_x))
    ctrl2_y = max(10.0, min(max_h - 10.0, ctrl2_y))

    points = []
    for step in range(1, steps + 1):
        normalized_t = step / steps
        smooth_t = normalized_t * normalized_t * (3.0 - 2.0 * normalized_t)
        pt_x, pt_y = calculate_cubic_bezier_point(start, (ctrl1_x, ctrl1_y), (ctrl2_x, ctrl2_y), end, smooth_t)

        if step < steps:
            decay = 1.0 - (normalized_t ** 1.5)
            pt_x += random.gauss(0, 0.5) * decay
            pt_y += random.gauss(0, 0.5) * decay
        else:
            pt_x = end[0]
            pt_y = end[1]

        points.append((max(0.0, min(max_w, pt_x)), max(0.0, min(max_h, pt_y))))

    return points


async def is_element_fully_loaded(element: Any, check_hit_point: bool = True) -> bool:
    if not element:
        return False
    try:
        # 🛡️ 优先检查可见性：若已可见，严禁触发 scroll_into_view_if_needed！
        # 否则页面滚动调整会立即触发 Twitter 将悬浮名片 (HoverCard) 强制卸载关闭，导致按钮在点击前被销毁
        if hasattr(element, "is_visible"):
            if not await element.is_visible():
                if hasattr(element, "scroll_into_view_if_needed"):
                    await element.scroll_into_view_if_needed(timeout=1000)
                if not await element.is_visible():
                    return False
        box = await element.bounding_box() if hasattr(element, "bounding_box") else None
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            return False
        if check_hit_point and hasattr(element, "evaluate"):
            hit_ok = await element.evaluate(
                """(el, point) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) return false;
                    const style = window.getComputedStyle(el);
                    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity || '1') <= 0.05) return false;
                    const x = point ? Math.max(rect.left + 1, Math.min(rect.right - 1, point.x)) : rect.left + rect.width / 2;
                    const y = point ? Math.max(rect.top + 1, Math.min(rect.bottom - 1, point.y)) : rect.top + rect.height / 2;
                    const topEl = document.elementFromPoint(x, y);
                    if (!topEl) return true;
                    return !!(el === topEl || el.contains(topEl) || topEl.contains(el));
                }""",
                {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2},
            )
            if not hit_ok:
                return False
        return True
    except Exception:
        return False


async def human_move_to_fast(page: Any, element: Any, personality: ProfilePersonality) -> bool:
    if not await is_element_fully_loaded(element):
        return False

    try:
        box = await element.bounding_box()
        if not box:
            return False

        vp = getattr(page, "viewport_size", None) or {"width": 1280, "height": 800}
        viewport = (float(vp.get("width", 1280)), float(vp.get("height", 800)))

        # 🛡️ 拟人高斯正态落点 (均值 0.5, 方差 0.11, 截断在 [0.18, 0.82]，相比均匀随机显著消除机器人特征)
        rel_x = min(max(random.gauss(0.5, 0.11), 0.18), 0.82)
        rel_y = min(max(random.gauss(0.5, 0.11), 0.18), 0.82)
        target_x = box["x"] + box["width"] * rel_x
        target_y = box["y"] + box["height"] * rel_y

        current_pos = getattr(personality, "current_mouse_pos", None)
        if current_pos and isinstance(current_pos, (tuple, list)) and len(current_pos) == 2:
            start_x, start_y = float(current_pos[0]), float(current_pos[1])
        else:
            start_x = max(10.0, target_x - random.uniform(30, 70))
            start_y = max(10.0, target_y - random.uniform(30, 70))

        steps = personality.mouse_steps
        points = generate_bezier_trajectory((start_x, start_y), (target_x, target_y), steps, viewport)
        total_pts = len(points)
        for idx, (x, y) in enumerate(points):
            await page.mouse.move(x, y)
            # 拟人人手速度曲线 (两头慢、中间快，接近落点时微调减速)
            progress = (idx + 1) / max(total_pts, 1)
            speed_mult = 1.0 - 0.38 * math.sin(progress * math.pi)
            step_delay = max(0.007, min(0.032, random.lognormvariate(-4.2, 0.22) * speed_mult))
            await asyncio.sleep(step_delay)

        personality.current_mouse_pos = (target_x, target_y)
        await element.hover()
        # 视线聚焦与微停顿：对数正态分布 (0.45s ~ 1.65s)
        dwell = max(0.45, min(1.65, random.lognormvariate(-0.15, 0.32)))
        await asyncio.sleep(dwell)
        return True
    except Exception:
        return False


async def safe_human_click(page: Any, element: Any, personality: ProfilePersonality) -> bool:
    if not element:
        return False

    # 基础可见性防护：若元素已不可见则直接返回
    try:
        if hasattr(element, "is_visible") and not await element.is_visible():
            return False
    except Exception:
        return False

    # 🛡️ 前置外链与外部官网卡片穿透护盾：绝对禁止点击跳出推特的外部链接与广告
    try:
        if hasattr(element, "evaluate"):
            is_external_jump = await element.evaluate("""
                el => {
                    let curr = el;
                    for (let i = 0; i < 6 && curr && curr !== document.body; i++) {
                        const testId = (curr.getAttribute('data-testid') || '').toLowerCase();
                        if (testId.includes('card.') || testId.includes('userurl')) {
                            return true;
                        }
                        if (curr.tagName === 'A') {
                            const target = (curr.getAttribute('target') || '').toLowerCase();
                            const href = (curr.getAttribute('href') || '').toLowerCase();
                            if (target === '_blank') {
                                return true;
                            }
                            if (href.startsWith('http://') || href.startsWith('https://')) {
                                if (!href.includes('x.com') && !href.includes('twitter.com')) {
                                    return true;
                                }
                            }
                            if (href.includes('t.co/') && (testId.includes('card') || (curr.closest && curr.closest('[data-testid*="card"]')))) {
                                return true;
                            }
                        }
                        curr = curr.parentElement;
                    }
                    return false;
                }
            """)
            if is_external_jump:
                print("  └─ 🛡️ [外链防御] 识别到该元素为外部官网跳转链接/卡片，安全拦截点击动作")
                return False
    except Exception:
        pass

    is_down = False
    try:
        moved_ok = await human_move_to_fast(page, element, personality)
        if moved_ok and hasattr(page, "mouse") and hasattr(page.mouse, "down"):
            await page.mouse.down()
            is_down = True
            # 🛡️ 真人按键按压时长：对数正态随机分布 (均值 ~75ms，截断在 [45ms, 135ms]，消除固定时长按键指纹)
            press_duration = max(0.045, min(0.135, random.lognormvariate(-2.65, 0.25)))
            if hasattr(personality, "press_duration") and personality.press_duration:
                p_min, p_max = personality.press_duration
                press_duration = max(p_min, min(p_max, press_duration))
            await asyncio.sleep(press_duration)
            await page.mouse.up()
            is_down = False
            return True
    except Exception:
        pass
    finally:
        if is_down and hasattr(page, "mouse") and hasattr(page.mouse, "up"):
            try:
                await page.mouse.up()
            except Exception:
                pass

    # 🛡️ 稳健兜底保障：若拟人贝塞尔微操由于 DPI 缩放或页面微偏移未能触发点击，
    # 立即平滑执行标准 element.click()，确保高价值目标不被跳过，恢复 9月7日版本稳定点击成功率
    try:
        if hasattr(element, "click"):
            await element.click(timeout=2500)
            return True
    except Exception:
        pass

    return False


async def human_type_text(page: Any, element: Any, text: str) -> None:
    """模拟真人按键打字，带有对数正态击键延迟与停顿"""
    try:
        await element.click()
        await asyncio.sleep(random.uniform(0.6, 1.2))

        for index, char in enumerate(text):
            await page.keyboard.type(char)
            dwell = max(0.04, min(0.35, random.lognormvariate(-2.3, 0.35)))
            await asyncio.sleep(dwell)
            if char in ".,!?;:\n":
                await asyncio.sleep(random.uniform(0.25, 0.55))
            elif char == " " and random.random() < 0.3:
                await asyncio.sleep(random.uniform(0.12, 0.28))
            if index > 0 and index % random.randint(5, 7) == 0:
                await asyncio.sleep(random.uniform(0.3, 0.6))
        await asyncio.sleep(random.uniform(0.8, 1.5))
    except Exception as err:
        logging.getLogger("laogu-ai-agent.x-automation").debug("human_type_text 打字非阻断提示: %s", err)


async def human_discrete_scroll(
    page: Any, distance: int | None = None, sleep_func: Any | None = None
) -> None:
    """模拟真人离散滚轮下翻，带有 Ease-Out 阻尼衰减"""
    try:
        raw_dist = distance if distance is not None else random.randint(350, 700)
        direction = 1 if raw_dist >= 0 else -1
        total_dist = abs(raw_dist)
        if total_dist <= 0:
            return

        async def _sleep(sec: float) -> None:
            if callable(sleep_func):
                await sleep_func(sec)
            else:
                await asyncio.sleep(sec)

        num_steps = max(2, min(8, int(total_dist / random.randint(75, 120))))
        weights = [(num_steps - i) ** 1.3 for i in range(num_steps)]
        weight_sum = sum(weights)
        raw_steps = [int(round(total_dist * (w / weight_sum))) for w in weights]

        diff = total_dist - sum(raw_steps)
        if raw_steps:
            raw_steps[0] += diff
        else:
            raw_steps = [total_dist]

        for amount in raw_steps:
            if amount <= 0:
                continue
            await page.mouse.wheel(0, amount * direction)
            await _sleep(random.uniform(0.08, 0.18))

        dwell_time = random.uniform(1.8, 3.2)
        if distance is None and random.random() < 0.35:
            dwell_time += random.uniform(1.5, 3.0)
        await _sleep(dwell_time)

        # 拟人视线回看：仅在自然闲逛下翻 (distance is None) 时 15% 概率向上轻微回滑 (60~120px) 并视线驻留 1.2~2.2 秒，打破单向机械线性滚动特征
        if distance is None and direction == 1 and total_dist >= 200 and random.random() < 0.15:
            back_dist = random.randint(60, 120)
            await _sleep(random.uniform(0.2, 0.5))
            await page.mouse.wheel(0, -back_dist)
            await _sleep(random.uniform(1.2, 2.2))
    except Exception as err:
        logging.getLogger("laogu-ai-agent.x-automation").debug("human_discrete_scroll 滚动缓冲: %s", err)


def human_lognormal_delay_sec(target_mean: float = 2.0, sigma: float = 0.35, min_sec: float = 0.5, max_sec: float = 12.0) -> float:
    """计算真人行为对数正态随机延迟秒数 (自然右偏长尾分布，有效避免均匀分布机器人指纹特征)"""
    try:
        mu = math.log(max(0.01, target_mean)) - 0.5 * (sigma ** 2)
        val = random.lognormvariate(mu, sigma)
        return max(min_sec, min(max_sec, val))
    except Exception:
        return random.uniform(min_sec, max_sec)


async def human_lognormal_delay(target_mean: float = 2.0, sigma: float = 0.35, min_sec: float = 0.5, max_sec: float = 12.0) -> float:
    """异步等待对数正态随机延迟"""
    sec = human_lognormal_delay_sec(target_mean, sigma, min_sec, max_sec)
    await asyncio.sleep(sec)
    return sec


class XAutomationEngine:
    AutomationConfig = AutomationConfig

    RATE_LIMIT_PATTERNS = (
        re.compile(r"rate limit", re.I),
        re.compile(r"too many requests", re.I),
        re.compile(r"temporarily restricted", re.I),
        re.compile(r"action blocked", re.I),
        re.compile(r"请求过于频繁"),
        re.compile(r"操作频率过高"),
        re.compile(r"速率限制"),
        re.compile(r"已被限制"),
        re.compile(r"レート制限"),
        re.compile(r"リクエスト回数が多すぎます"),
        re.compile(r"試行回数が多すぎます"),
        re.compile(r"一時的に制限されています"),
        re.compile(r"操作が制限されています"),
    )

    @classmethod
    def evaluate_time_window(
        cls,
        current_total: int,
        daily_limit: int,
        *,
        now: datetime | None = None,
        tz_offset_hours: int = 8,
    ) -> tuple[bool, str, int]:
        """智能时段窗口评估（统一采用 UTC+8 或指定时区，完整判定上午、下午及晚间时段）"""
        tz = timezone(timedelta(hours=tz_offset_hours))
        now_dt = now.astimezone(tz) if now is not None else datetime.now(tz)
        hour = now_dt.hour

        remaining_tasks = daily_limit - current_total
        if remaining_tasks <= 0:
            return False, "全天目标已达成", 0

        morning_target = int(daily_limit * 0.35)
        afternoon_target = int(daily_limit * 0.60)
        evening_target = daily_limit

        if 8 <= hour < 12:
            if current_total >= morning_target:
                return False, f"上午配额已达标 ({current_total}/{morning_target})，等待 14 点", 0
            return True, "上午窗口 (08:00 - 12:00)", morning_target

        elif 14 <= hour < 18:
            # 下午时段完整覆盖 14:00 ~ 18:00（含 14:00~16:00 与 16:00~18:00 两个下午子时段，杜绝下午提前误休眠）
            if current_total >= afternoon_target:
                return False, f"下午配额已达标 ({current_total}/{afternoon_target})，等待 18 点", 0
            sub_label = "14:00 - 16:00" if hour < 16 else "16:00 - 18:00"
            return True, f"下午窗口 ({sub_label})", afternoon_target

        elif 18 <= hour < 22:
            return True, "晚间窗口 (18:00 - 22:00)", evening_target

        else:
            return False, f"非工作时段 (当前时间: {now_dt.strftime('%H:%M')})", 0


    def __init__(self, cdp_url: str = "", config_path: str = "selectors_config.json", *, logger: logging.Logger | None = None, **kwargs: Any):
        target_cdp = cdp_url or kwargs.get("cdp_url") or ""
        if not str(target_cdp).strip():
            raise ValueError("cdp_url is required")

        self.cdp_url: str = str(target_cdp).strip()
        self.logger: logging.Logger = logger or logging.getLogger("laogu-ai-agent.x-automation")
        self.tag: str = "系统"
        self.config_path: str = config_path
        self.user_cache: dict[str, dict[str, Any]] = {}
        self.personality: ProfilePersonality = ProfilePersonality(self.cdp_url)
        self.progress_callback = kwargs.get("progress_callback")
        self.safety_guard: Any | None = kwargs.get("safety_guard")
        self.history_pool: PersistentHistoryPool = kwargs.get("history_pool") or PersistentHistoryPool.get_instance()
        self._session_processed_handles: set[str] = set()
        self.risk_artifact_dir: str = str(kwargs.get("risk_artifact_dir") or "")
        self._risk_reason: str = ""
        self._active_config: AutomationConfig | None = None
        self._comments_total = 0
        self.my_username: str = str(kwargs.get("my_username") or "").strip().lstrip("@")
        self._cleaned_unreciprocated_today: bool = False

        # ⚡ 底层数据流极速透视预筛状态池（实验性功能）
        self._scout_candidates: dict[str, dict[str, Any]] = {}
        self._scout_rejected_handles: set[str] = set()
        self._scout_rejection_reasons: dict[str, str] = {}


        # 🛡️ 极致细节：AI API 连续失败计数与熔断标志位[cite: 3, 4]
        self._consecutive_ai_failures = 0
        self._ai_circuit_broken = False
        self._active_target_leases: dict[str, Any] = {}

        # 🔗 顺藤摸瓜链式裂变追踪状态 (A➜B➜C 无尽链式关系网深度递进)
        self._chain_stack: list[str] = []
        self._chain_current_following_handle: str = ""

        # 🌿 原位自然休眠与单博主采摘饱和控制（仅在透视+深挖双开时生效）
        self._in_situ_list_active: bool = False
        self._in_situ_seed_handle: str = ""
        self._seed_harvest_counts: dict[str, int] = {}

        # 👥 扫博主粉丝模式：跨批次轮转索引与已扫描计数字典
        self._target_creator_idx: int = 0
        self._creator_scanned_counts: dict[str, int] = {}

        # 🛡️ 内存主动管理与崩溃自愈 (Anti-OOM & Self-Healing)
        self._is_page_crashed: bool = False
        self._last_renderer_gc_time: float = 0.0
        self._scroll_actions_since_last_gc: int = 0

        # ⏱️ 定时重新检索/刷新最新推文流状态跟踪
        self._last_search_refresh_time: float = 0.0

        # 🚨 风控熔断感知标志
        self._follow_limit_reached: bool = False
        self._consecutive_write_403_count: int = 0


    async def _cloud_claim_target(self, target_handle: str, config: AutomationConfig | None = None) -> tuple[bool, str]:
        cfg = config or getattr(self, "config", None)
        if not cfg or not getattr(cfg, "cloud_dedup_enabled", False):
            return True, "DISABLED"
        target_norm = (target_handle or "").strip().lstrip("@").lower()
        if not target_norm:
            return True, "EMPTY_TARGET"
        from uuid import uuid4
        lease_id = uuid4()
        try:
            allowed, reason = await asyncio.to_thread(
                _sync_cloud_claim_target,
                server_url=cfg.cloud_dedup_server_url,
                studio_token=cfg.cloud_dedup_studio_token,
                handle=target_norm,
                device_name=cfg.cloud_dedup_device_name,
                account_tag=getattr(cfg, "account_tag", self.tag),
                lease_id=lease_id,
            )
            if allowed:
                self._active_target_leases[target_norm] = lease_id
            return allowed, reason
        except Exception:
            return True, "FAIL_OPEN"

    async def _cloud_confirm_target(self, target_handle: str, config: AutomationConfig | None = None) -> bool:
        cfg = config or getattr(self, "config", None)
        if not cfg or not getattr(cfg, "cloud_dedup_enabled", False):
            return False
        target_norm = (target_handle or "").strip().lstrip("@").lower()
        if target_norm not in self._active_target_leases:
            return False
        lease_id = self._active_target_leases.get(target_norm)
        try:
            ok = await asyncio.to_thread(
                _sync_cloud_confirm_target,
                server_url=cfg.cloud_dedup_server_url,
                studio_token=cfg.cloud_dedup_studio_token,
                handle=target_norm,
                device_name=cfg.cloud_dedup_device_name,
                account_tag=getattr(cfg, "account_tag", self.tag),
                lease_id=lease_id,
            )
            if ok:
                self._active_target_leases.pop(target_norm, None)
            return bool(ok)
        except Exception:
            return False

    def _report_progress(self, **values: Any) -> None:
        callback = getattr(self, "progress_callback", None)
        if not callable(callback):
            return
        try:
            progress = {
                key: max(0, int(value or 0))
                for key, value in values.items()
                if key in {"processed_count", "likes", "follows", "comments", "scanned_posts"}
            }
            callback(progress)
        except Exception as exc:
            self.logger.debug("Automation progress callback failed: %s", exc)

    def _print(self, msg: str) -> None:
        """带精确时间戳的格式化日志输出 (对 Windows 控制台多语言/Emoji 进行全安全容错)"""
        now_str = datetime.now().strftime("%H:%M:%S")
        out_msg = f"[{now_str}] [账号: {self.tag}] {msg}"
        try:
            print(out_msg)
        except UnicodeEncodeError:
            try:
                import sys
                enc = sys.stdout.encoding or "utf-8"
                print(out_msg.encode(enc, errors="replace").decode(enc))
            except Exception:
                try:
                    print(out_msg.encode("ascii", errors="replace").decode("ascii"))
                except Exception:
                    pass

    async def _check_run_control(self) -> None:
        guard = getattr(self, "safety_guard", None)
        if guard is None:
            return
        if bool(getattr(guard, "is_cancelled", lambda: False)()):
            raise AutomationStopped("控制中心已取消当前自动化任务")
        if bool(getattr(guard, "is_paused", lambda: False)()):
            self._print("⏸️ [控制中心] 自动化任务已暂停，等待恢复...")
            while bool(getattr(guard, "is_paused", lambda: False)()):
                await asyncio.sleep(1.0)
                if bool(getattr(guard, "is_cancelled", lambda: False)()):
                    raise AutomationStopped("控制中心已取消当前自动化任务")
            self._print("▶️ [控制中心] 自动化任务已恢复，继续执行。")

    async def _sleep_with_control(self, seconds: float) -> None:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            await self._check_run_control()
            interval = min(1.0, remaining)
            await asyncio.sleep(interval)
            remaining -= interval

    async def _sleep_with_natural_dwell(self, page: Any, seconds: float) -> None:
        """带拟人保活与视口微游弋的受控停顿：
        在停顿期间每 4~6 秒执行微量鼠标自然抖动，
        防止 Chromium / Twitter WebSocket 长连接因纯静止超时中断，
        同时彻底打散纯静止的机器指纹。
        """
        remaining = max(0.0, float(seconds))
        dwell_tick = 0
        while remaining > 0:
            await self._check_run_control()
            interval = min(1.0, remaining)
            await asyncio.sleep(interval)
            remaining -= interval
            dwell_tick += 1
            if dwell_tick >= 5 and remaining >= 3.0:
                dwell_tick = 0
                try:
                    mouse = getattr(page, "mouse", None)
                    if mouse and hasattr(mouse, "move"):
                        rx = random.randint(220, 580)
                        ry = random.randint(220, 480)
                        await mouse.move(rx, ry, steps=random.randint(4, 8))
                except Exception:
                    pass

    async def _capture_risk_snapshot(self, page: Any, status: str) -> str:
        destination = str(getattr(self, "risk_artifact_dir", "") or "").strip()
        if not destination or page is None:
            return ""
        try:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            safe_tag = re.sub(r"[^A-Za-z0-9_-]+", "_", self.tag or "profile")[:80]
            filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_tag}-{status.lower()}.png"
            target = directory / filename
            if hasattr(page, "screenshot"):
                await page.screenshot(path=str(target), full_page=False)

            # 🛡️ 磁盘空间保护：自动修剪旧快照，最多保留最新的 50 张截图，杜绝磁盘写满
            try:
                snapshots = sorted(directory.glob("*.png"), key=lambda p: p.stat().st_mtime)
                if len(snapshots) > 50:
                    for old_shot in snapshots[:-50]:
                        try:
                            old_shot.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

            return str(target)
        except Exception as exc:
            self.logger.info("Automation risk screenshot deferred: %s", exc)
            return ""

    async def _allow_action(self, action: str, target_key: str = "") -> bool:
        await self._check_run_control()
        if self._risk_reason:
            risk = self._risk_reason
            self._risk_reason = ""
            self._print(f"🚨 [即时断电熔断] 检测到底层接口已风控拦截 ({risk})，拒绝执行 {action} 动作！")
            raise RateLimitPause(900, reason=risk)
        config = self._active_config
        if config is not None:
            if getattr(config, "dry_run", False):
                self._print(f"🔎 预演模式：跳过 {action} 动作")
                return False
            if not bool(getattr(config, f"allow_{action}", True)):
                self._print(f"🛡️ 控制中心策略：已禁止 {action} 动作")
                return False
        guard = getattr(self, "safety_guard", None)
        if guard is None:
            return True
        decision = guard.allow_action(action, target_key)
        if bool(getattr(decision, "allowed", decision)):
            return True
        self._print(f"🛡️ 控制中心配额策略拦截：跳过 {action} 动作 - {getattr(decision, 'reason', '')}")
        return False

    def _record_action(self, action: str, target_key: str = "") -> None:
        guard = getattr(self, "safety_guard", None)
        if guard is None:
            return
        try:
            guard.record_action(action, target_key)
        except Exception as exc:
            self.logger.info("Automation safety action record deferred: %s", exc)

    @staticmethod
    async def _article_target_key(article: Any, fallback: str = "") -> str:
        try:
            if hasattr(article, "query_selector"):
                link = await article.query_selector('a[href*="/status/"]')
                href = await link.get_attribute("href") if link else ""
                if href:
                    return f"url:{href.split('?')[0]}"
        except Exception:
            pass
        return f"handle:{fallback}" if fallback else ""

    async def _dismiss_modal_dialogs(self, page: Any) -> None:
        try:
            if hasattr(page, "query_selector"):
                close_btn = await page.query_selector(
                    'button[data-testid="app-bar-close"], '
                    'button[aria-label="Close"], button[aria-label="关闭"], '
                    'div[role="dialog"] button:has-text("Not now"), '
                    'div[role="dialog"] button:has-text("以后再说"), '
                    'button[data-testid="confirmationSheetCancel"]'
                )
                if close_btn and hasattr(close_btn, "is_visible") and await close_btn.is_visible():
                    await close_btn.click()
                    await asyncio.sleep(0.5)
            keyboard = getattr(page, "keyboard", None)
            if keyboard is not None and hasattr(keyboard, "press"):
                await keyboard.press("Escape")
        except Exception:
            pass

    async def _casual_check_notifications(self, page: Any) -> None:
        try:
            self._print("🔔 [拟人生活流] 顺手点击通知栏 (Notifications) 查看是否有互动提醒...")
            notif_link = await page.query_selector('a[data-testid="AppTabBar_Notifications_Link"], a[href="/notifications"]')
            if notif_link and await is_element_fully_loaded(notif_link):
                if await safe_human_click(page, notif_link, self.personality):
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                    await self._scroll_with_control(page, distance=random.randint(150, 250))
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    try:
                        await page.go_back()
                    except Exception:
                        pass
        except Exception as e:
            self.logger.debug("通知栏拟人浏览非阻断提示: %s", e)

    async def _random_media_consumption(self, page: Any) -> None:
        """拟人图文大图消费：随机点击推文附图放大欣赏，模拟真人富媒体消费习惯（严格防外链）"""
        try:
            # 🛡️ 严格限定仅识别推特原生用户照片，严禁抓取外部官网链接卡片 (card) 缩略图
            images = await page.query_selector_all('div[data-testid="tweetPhoto"] img')
            if images and random.random() < 0.45:
                target_img = random.choice(images[:4])
                if await is_element_fully_loaded(target_img):
                    is_in_card = False
                    if hasattr(target_img, "evaluate"):
                        try:
                            is_in_card = await target_img.evaluate("el => Boolean(el.closest('[data-testid*=\"card\"], a[target=\"_blank\"]'))")
                        except Exception:
                            pass
                    if not is_in_card:
                        self._print("🖼️ [富媒体消费] 首页闲逛：点击查看推文大图，模拟真实人类浏览...")
                        if await safe_human_click(page, target_img, self.personality):
                            await self._sleep_with_control(random.uniform(2.8, 4.5))
                            close_btn = await page.query_selector('button[aria-label="Close"], button[aria-label="关闭"], div[aria-label="Close"]')
                            if close_btn and await is_element_fully_loaded(close_btn):
                                await safe_human_click(page, close_btn, self.personality)
                            else:
                                keyboard = getattr(page, "keyboard", None)
                                if keyboard:
                                    await keyboard.press("Escape")
                            await self._sleep_with_control(random.uniform(1.2, 2.0))
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self.logger.debug("大图消费非阻断提示: %s", e)

    async def _simulate_natural_reading(self, page: Any, article: Any, handle: str, reason: str = "") -> None:
        """
        拟人内容消费掩护流：
        遇到大V、认证机构或非目标推文时，不直接 0ms 暴击跳过，而是将光标自然移入推文区域，
        模拟人眼阅读停留 1.4~2.4 秒，偶发点击“显示更多 (Show more)”展开长文或看大图，
        充当天然的反风控伪装掩护，并在动作执行层严格不产生关注与点赞。
        """
        try:
            # 1. 模拟视线移动：将光标自然移入推文卡片区域
            try:
                user_name_link = await article.query_selector('div[data-testid="User-Name"]')
                if user_name_link and await is_element_fully_loaded(user_name_link):
                    await human_move_to_fast(page, user_name_link, self.personality)
            except Exception:
                pass

            # 2. 模拟阅读停留 (1.4 ~ 2.4s)
            dwell_time = random.uniform(1.4, 2.4)
            reason_str = f" [{reason}]" if reason else ""
            self._print(f"👀 [拟人阅读掩护] 偶遇大V/机构推文 @{handle}{reason_str}，模拟真人阅览中 ({dwell_time:.1f}s)，不执行关注与点赞")
            await self._sleep_with_control(dwell_time)

            # 3. 20% 概率：点击长推文“显示更多” (Show more)
            if random.random() < 0.20:
                try:
                    show_more_sel = 'a[data-testid="tweet-text-show-more-link"], button:has-text("Show more"), [role="button"]:has-text("Show more"), [role="button"]:has-text("显示更多"), [role="button"]:has-text("もっと見る")'
                    show_more_btn = await article.query_selector(show_more_sel)
                    if show_more_btn and await is_element_fully_loaded(show_more_btn):
                        if await safe_human_click(page, show_more_btn, self.personality):
                            read_more_pause = random.uniform(1.2, 2.0)
                            self._print(f"  └─ 📖 [拟人深入阅读] 顺手展开长推文阅览中 ({read_more_pause:.1f}s)...")
                            await self._sleep_with_control(read_more_pause)
                except CONTROL_EXCEPTION_TYPES:
                    raise
                except Exception as sm_err:
                    self.logger.debug("展开长推文非阻断提示: %s", sm_err)

            # 4. 15% 概率：顺便看一眼推文大图 (严格限定原生相册，绝不点官网预览卡片)
            if random.random() < 0.15:
                try:
                    img_el = await article.query_selector('div[data-testid="tweetPhoto"] img')
                    if img_el and await is_element_fully_loaded(img_el):
                        is_in_card = False
                        if hasattr(img_el, "evaluate"):
                            try:
                                is_in_card = await img_el.evaluate("el => Boolean(el.closest('[data-testid*=\"card\"], a[target=\"_blank\"]'))")
                            except Exception:
                                pass
                        if not is_in_card and await safe_human_click(page, img_el, self.personality):
                            await self._sleep_with_control(random.uniform(2.0, 3.2))
                            close_btn = await page.query_selector('button[aria-label="Close"], button[aria-label="关闭"], div[aria-label="Close"]')
                            if close_btn and await is_element_fully_loaded(close_btn):
                                await safe_human_click(page, close_btn, self.personality)
                            else:
                                keyboard = getattr(page, "keyboard", None)
                                if keyboard:
                                    await keyboard.press("Escape")
                            await self._sleep_with_control(random.uniform(0.8, 1.5))
                except CONTROL_EXCEPTION_TYPES:
                    raise
                except Exception as img_err:
                    self.logger.debug("拟人看图非阻断提示: %s", img_err)

        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as read_err:
            self.logger.debug("模拟自然阅读异常: %s", read_err)

    async def _do_cold_start_warmup(self, page: Any, config: AutomationConfig) -> None:
        """冷启动真人拟态预热浏览：在正式拓客前在首页信息流中自然阅读、滚动与查看，大幅降低初始风控几率。"""
        min_s = getattr(config, "cold_start_warmup_min_seconds", 45)
        max_s = getattr(config, "cold_start_warmup_max_seconds", 60)
        if hasattr(config, "cold_start_warmup_duration_seconds") and not getattr(config, "cold_start_warmup_min_seconds", None):
            dur = getattr(config, "cold_start_warmup_duration_seconds", 45)
            min_s, max_s = min(dur, 45), max(dur, 60)
        min_s, max_s = max(10, min(300, min(min_s, max_s))), max(10, min(300, max(min_s, max_s)))
        target_seconds = random.randint(min_s, max_s)
        self._print("=" * 60)
        self._print(f"🌱 [冷启动预热] 开始真人拟态首页浏览预热（随机抽样预热 {target_seconds} 秒 [区间 {min_s}~{max_s}s]）...")
        self._print("🌱 正在模拟真人打开推特后的自然阅览、信息流滚动与图文浏览...")
        self._print("=" * 60)

        loop = asyncio.get_event_loop()
        start_time = loop.time()
        round_idx = 1

        while (loop.time() - start_time) < target_seconds:
            await self._check_run_control()
            await self._assert_no_challenge(page)

            # 1. 拟人向下平滑离散滚动
            scroll_dist = random.randint(280, 580)
            await self._scroll_with_control(page, distance=scroll_dist)
            read_pause = random.uniform(2.5, 4.8)
            await self._sleep_with_control(read_pause)

            # 2. 偶发拟人看大图 (30% 概率)
            if random.random() < 0.30:
                await self._random_media_consumption(page)

            # 3. 偶发拟人查看通知 (20% 概率，仅首轮可能触发)
            if round_idx == 1 and random.random() < 0.20:
                await self._casual_check_notifications(page)

            elapsed = int(loop.time() - start_time)
            self._print(f"  └─ 📖 [预热浏览中] 正在自然阅读首页推荐内容 (已持续 {elapsed}/{target_seconds} 秒)...")
            round_idx += 1

        self._print("=" * 60)
        self._print("✅ [冷启动预热] 首页自然阅读预热完成！正式切入目标拓客任务流。")
        self._print("=" * 60)

    async def _verify_follow_success(self, page: Any, container: Any = None) -> tuple[bool, str]:
        await asyncio.sleep(random.uniform(0.8, 1.4))
        try:
            if hasattr(page, "query_selector"):
                toast = await page.query_selector('div[data-testid="toast"]')
                if toast and hasattr(toast, "is_visible") and await toast.is_visible():
                    toast_text = (await toast.inner_text() or "").strip() if hasattr(toast, "inner_text") else ""
                    rate_limit_kw = self._find_rate_limit(toast_text)
                    if rate_limit_kw or any(w in toast_text.lower() for w in [
                        "unable to follow", "cannot follow", "limit reached", "关注受限", "关注上限", "频次限制", "操作过于频繁",
                        "フォローできません", "上限に達しました", "フォロー上限", "一時的に制限", "制限されています"
                    ]):
                        self._print(f"🚨 [风控限流拦截] X 平台拦截关注动作: {toast_text}")
                        return False, f"RATE_LIMITED: {toast_text}"

            # 严格限定作用域：优先使用传入的资料卡/名片容器，未传入时自动提取主页头部，杜绝全局检索命中侧边栏大V推荐
            scope = container
            if not scope and hasattr(page, "query_selector"):
                scope = await page.query_selector('div[data-testid="UserProfileHeader_Root"], div[data-testid="primaryColumn"]')
            if not scope:
                scope = page

            if hasattr(scope, "query_selector"):
                following_btn = await scope.query_selector('button[data-testid$="-unfollow"]')
                if following_btn and (not hasattr(following_btn, "is_visible") or await following_btn.is_visible()):
                    return True, "FOLLOWED"

                pending_btn = await scope.query_selector(
                    'button[data-testid*="pending"], button[data-testid*="-cancel"], '
                    'button[aria-label*="Pending"], button[aria-label*="Requested"], '
                    'button[aria-label*="リクエスト中"], button[aria-label*="已申请"]'
                )
                if pending_btn and (not hasattr(pending_btn, "is_visible") or await pending_btn.is_visible()):
                    return True, "REQUESTED"

                reverted_btn = await scope.query_selector('button[data-testid$="-follow"]')
                if reverted_btn and (not hasattr(reverted_btn, "is_visible") or await reverted_btn.is_visible()):
                    self._print("⚠️ [关注回退] 关注按钮未翻转（被平台静默拒绝并回退为 Follow），疑似软限流")
                    return False, "SILENT_REJECTION"

            return True, "LIKELY_FOLLOWED"
        except Exception as err:
            self.logger.debug("_verify_follow_success error: %s", err)
            return True, "VERIFY_ERROR_FALLBACK"

    def _is_x_page_url(self, url: str) -> bool:
        u = str(url or "").lower()
        return "x.com" in u or "twitter.com" in u

    @staticmethod
    def _is_crash_error(err: Any) -> bool:
        """精准识别 Chromium 渲染器崩溃 (Sad Tab) 与 OOM 相关异常（中 / 日 / 英全语言覆盖）"""
        if err is None:
            return False
        msg = str(err).lower()
        crash_keywords = (
            # 英文标准内核错误与提示
            "page crashed",
            "target crashed",
            "crashed",
            "out of memory",
            "target closed",
            "session closed",
            "context destroyed",
            "context was destroyed",
            "has been closed",
            "browser has been closed",
            "aw, snap",
            # 中文 Chromium 崩溃与提示
            "内存不足",
            "页面崩溃",
            "崩溃",
            "糟糕，页面崩溃了",
            "错误代码：内存不足",
            "错误代码: 内存不足",
            "错误代码：out of memory",
            "错误代码: out of memory",
            # 日文 Chromium 崩溃与提示
            "エラー コード: out of memory",
            "エラーコード: out of memory",
            "問題が発生しました",
            "クラッシュ",
            "メモリ不足",
        )
        return any(kw in msg for kw in crash_keywords)

    def _setup_page_listeners(self, page: Any) -> None:
        """为活跃标签页挂载网络监听、弹窗拒绝拦截与崩溃捕获守卫 (防止重复注册)"""
        if not page or not hasattr(page, "on"):
            return
        if getattr(page, "_laogu_listeners_attached", False):
            return
        setattr(page, "_laogu_listeners_attached", True)
        try:
            page.on("response", self._response_callback)
        except Exception:
            pass
        try:
            page.on("response", lambda res: asyncio.create_task(self._handle_response_interception(res)))
        except Exception:
            pass
        try:
            page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        except Exception:
            pass
        try:
            def _on_crash():
                self._is_page_crashed = True
                self._print("🚨 [标签页崩溃告警] 监听到 Chromium 渲染进程异常终止 (Out of Memory / Page Crashed)！")
            page.on("crash", _on_crash)
        except Exception:
            pass

    async def _maybe_purge_renderer_memory(self, page: Any, force: bool = False) -> bool:
        """
        主动内存泄放与垃圾回收 (Anti-OOM Proactive Memory Purge)：
        1. 监控滚屏次数与运行时间；
        2. 每累计 12 次滚屏或 180 秒（或 force=True）触发一次；
        3. 向 CDP 下发 HeapProfiler.collectGarbage 强制 V8 进行 Mark-Sweep-Compact 全量垃圾回收；
        4. 评估 window.gc() 辅助泄放媒体与虚拟 DOM 缓存；
        5. 防止长时间无限滚动引发的 Chromium 'Out of Memory' 哭脸崩溃。
        """
        now = time.time()
        last_gc = getattr(self, "_last_renderer_gc_time", 0.0)
        scroll_count = getattr(self, "_scroll_actions_since_last_gc", 0)

        if not force and scroll_count < 12 and (now - last_gc) < 180.0:
            return False

        self._last_renderer_gc_time = now
        self._scroll_actions_since_last_gc = 0

        purged = False
        try:
            context = getattr(page, "context", None)
            if context and hasattr(context, "new_cdp_session"):
                cdp = await context.new_cdp_session(page)
                try:
                    await cdp.send("HeapProfiler.collectGarbage")
                    purged = True
                except Exception:
                    pass
                finally:
                    try:
                        await cdp.detach()
                    except Exception:
                        pass
        except Exception as cdp_err:
            self.logger.debug("CDP 垃圾回收指令忽略: %s", cdp_err)

        try:
            if page and hasattr(page, "evaluate"):
                await page.evaluate("""() => {
                    try {
                        if (window.gc) { window.gc(); }
                        const media = document.querySelectorAll('video, audio');
                        media.forEach(m => {
                            try {
                                const r = m.getBoundingClientRect();
                                if (r.bottom < -400 || r.top > (window.innerHeight || 800) + 400) {
                                    m.pause();
                                }
                            } catch(e) {}
                        });
                    } catch(e) {}
                }""")
                purged = True
        except Exception:
            pass

        if purged:
            self.logger.debug("🧹 已触发 Chromium 渲染进程主动垃圾回收 (Heap GC)，释放脱落 DOM 与图片视频缓存")
        return purged

    async def _recover_crashed_page(self, context: Any, old_page: Any = None, fallback_url: str = "https://x.com/home") -> Any:
        """
        Chromium 标签页内存溢出崩溃 (Sad Tab / Out of Memory) 无损自愈器：
        1. 安全关闭崩溃标签页；
        2. 在同一个已连接浏览器上下文中创建全新标签页（派生全新干净的 Renderer 进程与 4GB 内存配额）；
        3. 重新挂载核心事件监听（response, dialog, crash）；
        4. 导航至指定目标 URL 并重置焦点仿真；
        5. 返回全新的活跃 Page 实例，任务原地继续推进。
        """
        self._print("🚨 [Sad Tab 恢复] 检测到页面由于内存溢出或渲染器崩溃，启动自动无损自愈...")
        if old_page:
            try:
                if not getattr(old_page, "is_closed", lambda: False)():
                    await old_page.close()
            except Exception:
                pass

        new_page = None
        last_rec_err = None
        for attempt in range(1, 4):
            try:
                new_page = await context.new_page()
                if new_page:
                    break
            except Exception as np_err:
                last_rec_err = np_err
                err_msg = str(np_err).lower()
                if "createtarget" in err_msg or "failed to open a new tab" in err_msg or "protocol error" in err_msg:
                    await asyncio.sleep(1.0)
                    for p in getattr(context, "pages", []):
                        if not getattr(p, "is_closed", lambda: False)():
                            new_page = p
                            break
                    if new_page:
                        break
                else:
                    if attempt >= 3:
                        raise np_err
                    await asyncio.sleep(1.0)

        if not new_page and last_rec_err:
            raise last_rec_err

        self._setup_page_listeners(new_page)
        self._is_page_crashed = False

        self._print(f"🌐 [自愈导航] 正在新标签页中平滑重新载入: {fallback_url} ...")
        try:
            await new_page.goto(fallback_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2.0, 3.5))
        except Exception as nav_err:
            self.logger.warning("新标签页导航异常: %s，尝试兜底导航至主页", nav_err)
            try:
                await new_page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass

        await self._ensure_page_focus_emulation(new_page)
        if getattr(self, "_active_config", None) is not None:
            await self._ensure_cdp_locale_and_timezone_override(new_page, self._active_config)
            await self._ensure_video_stream_blocking(new_page, self._active_config)
        self._last_renderer_gc_time = time.time()
        self._scroll_actions_since_last_gc = 0
        return new_page

    async def _ensure_page_focus_emulation(self, page: Any) -> None:
        """🛡️ 后台静默运行焦点保持：通过 CDP 注入焦点仿真，使网页保持 hasFocus=true 且不抢占操作系统的物理鼠标与窗口前台。"""
        try:
            if getattr(page, "_cdp_focus_attached", None) is True:
                return
            context = getattr(page, "context", None)
            if context and hasattr(context, "new_cdp_session"):
                cdp = await context.new_cdp_session(page)
                if hasattr(cdp, "send"):
                    await cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
                setattr(page, "_cdp_focus_attached", True)
        except Exception as e:
            self.logger.debug("CDP 焦点仿真启用提示: %s", e)

    async def _ensure_cdp_locale_and_timezone_override(self, page: Any, config: AutomationConfig) -> None:
        """🛡️ CDP 动态时区与语言覆盖（消灭 IP 与时区地域冲突指纹）：
        当用户开启该配置时，通过 CDP 向当前页面内核注入 Emulation.setTimezoneOverride 与 Emulation.setLocaleOverride，
        确保网页 JavaScript (Intl.DateTimeFormat、new Date()、navigator.language) 探测到的时区与代理 IP 归属地 100% 一致。
        """
        if not getattr(config, "cdp_timezone_override_enabled", False):
            return
        try:
            if getattr(page, "_cdp_timezone_attached", None) is True:
                return
            context = getattr(page, "context", None)
            if not context or not hasattr(context, "new_cdp_session"):
                return

            tz_choice = str(getattr(config, "cdp_override_timezone", "auto") or "auto").strip()
            target_timezone = tz_choice
            target_locale = getattr(config, "cdp_override_locale", "") or ""

            # 常用时区 -> 默认语言映射表
            tz_locale_map = {
                "Asia/Tokyo": "ja-JP",
                "America/New_York": "en-US",
                "America/Los_Angeles": "en-US",
                "America/Chicago": "en-US",
                "America/Denver": "en-US",
                "America/Phoenix": "en-US",
                "Asia/Hong_Kong": "zh-HK",
                "Asia/Taipei": "zh-TW",
                "Asia/Singapore": "en-SG",
                "Europe/London": "en-GB",
                "Europe/Berlin": "de-DE",
                "Europe/Paris": "fr-FR",
                "Europe/Amsterdam": "nl-NL",
                "Europe/Rome": "it-IT",
                "Europe/Madrid": "es-ES",
                "Europe/Moscow": "ru-RU",
                "Asia/Seoul": "ko-KR",
                "Australia/Sydney": "en-AU",
                "America/Toronto": "en-CA",
                "America/Vancouver": "en-CA",
                "Asia/Kolkata": "en-IN",
                "America/Sao_Paulo": "pt-BR",
                "Asia/Bangkok": "th-TH",
                "Asia/Kuala_Lumpur": "ms-MY",
                "Asia/Manila": "en-PH",
                "Asia/Ho_Chi_Minh": "vi-VN",
                "Asia/Shanghai": "zh-CN",
            }
            country_tz_map = {
                "JP": ("Asia/Tokyo", "ja-JP"),
                "US": ("America/New_York", "en-US"),
                "HK": ("Asia/Hong_Kong", "zh-HK"),
                "TW": ("Asia/Taipei", "zh-TW"),
                "SG": ("Asia/Singapore", "en-SG"),
                "GB": ("Europe/London", "en-GB"),
                "UK": ("Europe/London", "en-GB"),
                "KR": ("Asia/Seoul", "ko-KR"),
                "DE": ("Europe/Berlin", "de-DE"),
                "FR": ("Europe/Paris", "fr-FR"),
                "NL": ("Europe/Amsterdam", "nl-NL"),
                "IT": ("Europe/Rome", "it-IT"),
                "ES": ("Europe/Madrid", "es-ES"),
                "RU": ("Europe/Moscow", "ru-RU"),
                "CA": ("America/Toronto", "en-CA"),
                "AU": ("Australia/Sydney", "en-AU"),
                "IN": ("Asia/Kolkata", "en-IN"),
                "BR": ("America/Sao_Paulo", "pt-BR"),
                "MY": ("Asia/Kuala_Lumpur", "ms-MY"),
                "PH": ("Asia/Manila", "en-PH"),
                "TH": ("Asia/Bangkok", "th-TH"),
                "VN": ("Asia/Ho_Chi_Minh", "vi-VN"),
                "CN": ("Asia/Shanghai", "zh-CN"),
            }

            if tz_choice.lower() == "auto":
                if getattr(self, "_cached_detected_timezone", None):
                    target_timezone = self._cached_detected_timezone
                    target_locale = getattr(self, "_cached_detected_locale", "") or "ja-JP"
                else:
                    # 自动探测模式：通过页面直接发起网络请求探测代理真实出口 IP（完全基于出口网络，杜绝根据节点名称猜测）
                    detected_tz = None
                    detected_cc = None
                    detected_ip = ""
                    if hasattr(page, "evaluate"):
                        try:
                            probe_script = """
                            async () => {
                                const endpoints = [
                                    "https://ipwho.is/",
                                    "https://api.ip.sb/geoip",
                                    "https://ipapi.co/json/",
                                    "https://freeipapi.com/api/json"
                                ];
                                for (const url of endpoints) {
                                    try {
                                        const ctrl = new AbortController();
                                        const tid = setTimeout(() => ctrl.abort(), 2500);
                                        const res = await fetch(url, { signal: ctrl.signal });
                                        clearTimeout(tid);
                                        if (res.ok) {
                                            const d = await res.json();
                                            const ip = d.ip || d.ipAddress || d.query || '';
                                            let tz = '';
                                            if (d.timezone) {
                                                if (typeof d.timezone === 'string') tz = d.timezone;
                                                else if (d.timezone.id) tz = d.timezone.id;
                                            } else if (Array.isArray(d.timeZones) && d.timeZones.length > 0) {
                                                tz = d.timeZones[0];
                                            }
                                            const cc = (d.country_code || d.countryCode || '').toUpperCase();
                                            if (tz || cc) {
                                                return { ip: ip, timezone: tz, country_code: cc };
                                            }
                                        }
                                    } catch(e) {}
                                }
                                return null;
                            }
                            """
                            probe_res = await page.evaluate(probe_script)
                            if isinstance(probe_res, dict):
                                detected_ip = str(probe_res.get("ip") or "").strip()
                                detected_tz = str(probe_res.get("timezone") or "").strip()
                                detected_cc = str(probe_res.get("country_code") or "").strip().upper()
                        except Exception:
                            pass

                    if detected_tz:
                        target_timezone = detected_tz
                        target_locale = tz_locale_map.get(detected_tz, "")
                        if not target_locale and detected_cc in country_tz_map:
                            target_locale = country_tz_map[detected_cc][1]
                        elif not target_locale:
                            if detected_tz.startswith("America/"):
                                target_locale = "en-US"
                            elif detected_tz.startswith("Europe/"):
                                target_locale = "en-GB"
                            else:
                                target_locale = "ja-JP"
                    elif detected_cc and detected_cc in country_tz_map:
                        target_timezone, target_locale = country_tz_map[detected_cc]
                    else:
                        # 探测超时或无代理，默认使用出海首选日本东京
                        target_timezone = "Asia/Tokyo"
                        target_locale = "ja-JP"

                    self._cached_detected_timezone = target_timezone
                    self._cached_detected_locale = target_locale
                    if detected_ip:
                        self._print(f"🌐 [代理出口IP探测] 成功探测到真实出口 IP: {detected_ip} (归属地: {detected_cc or '未知'} | 自动对齐时区: {target_timezone} | 语言: {target_locale})")
            else:
                if not target_locale and target_timezone in tz_locale_map:
                    target_locale = tz_locale_map[target_timezone]

            tz_geo_map = {
                "Asia/Tokyo": (35.6762, 139.6503),
                "America/New_York": (40.7128, -74.0060),
                "America/Los_Angeles": (34.0522, -118.2437),
                "America/Chicago": (41.8781, -87.6298),
                "America/Denver": (39.7392, -104.9903),
                "America/Phoenix": (33.4484, -112.0740),
                "Asia/Hong_Kong": (22.3193, 114.1694),
                "Asia/Taipei": (25.0330, 121.5654),
                "Asia/Singapore": (1.3521, 103.8198),
                "Europe/London": (51.5074, -0.1278),
                "Europe/Berlin": (52.5200, 13.4050),
                "Europe/Paris": (48.8566, 2.3522),
                "Europe/Amsterdam": (52.3676, 4.9041),
                "Europe/Rome": (41.9028, 12.4964),
                "Europe/Madrid": (40.4168, -3.7038),
                "Europe/Moscow": (55.7558, 37.6173),
                "Asia/Seoul": (37.5665, 126.9780),
                "Australia/Sydney": (-33.8688, 151.2093),
                "America/Toronto": (43.6532, -79.3832),
                "America/Vancouver": (49.2827, -123.1207),
                "Asia/Kolkata": (22.5726, 88.3639),
                "America/Sao_Paulo": (-23.5505, -46.6333),
                "Asia/Bangkok": (13.7563, 100.5018),
                "Asia/Kuala_Lumpur": (3.1390, 101.6869),
                "Asia/Manila": (14.5995, 120.9842),
                "Asia/Ho_Chi_Minh": (10.8231, 106.6297),
                "Asia/Shanghai": (31.2304, 121.4737),
            }

            cdp = await context.new_cdp_session(page)
            if hasattr(cdp, "send"):
                await cdp.send("Emulation.setTimezoneOverride", {"timezoneId": target_timezone})
                if target_locale:
                    try:
                        await cdp.send("Emulation.setLocaleOverride", {"locale": target_locale})
                    except Exception as loc_err:
                        self.logger.debug("CDP 语言覆盖忽略 (可能当前内核不支持或已对齐): %s", loc_err)
                if getattr(config, "cdp_geolocation_override_enabled", True) and target_timezone in tz_geo_map:
                    lat, lon = tz_geo_map[target_timezone]
                    try:
                        await cdp.send("Emulation.setGeolocationOverride", {
                            "latitude": lat,
                            "longitude": lon,
                            "accuracy": 100,
                        })
                    except Exception as geo_err:
                        self.logger.debug("CDP 地理位置覆盖忽略: %s", geo_err)
            setattr(page, "_cdp_timezone_attached", True)
            geo_info = f" GPS: {tz_geo_map[target_timezone]}" if getattr(config, "cdp_geolocation_override_enabled", True) and target_timezone in tz_geo_map else ""
            self._print(f"🛡️ [CDP防风控] 已成功动态覆盖浏览器时区/语言/GPS: {target_timezone} ({target_locale or '默认'}{geo_info})")
            self.logger.info("CDP 动态时区与语言覆盖成功: timezone=%s locale=%s%s", target_timezone, target_locale, geo_info)
        except Exception as e:
            self.logger.debug("CDP 时区覆盖执行提示: %s", e)

    async def _ensure_video_stream_blocking(self, page: Any, config: AutomationConfig) -> None:
        """
        极速省流防卡：底层阻断非必要大体积视频流 (Anti-Lag Video Stream Blocker)
        1. 默认开启：可在控制中心任务设置中随时自定义开启或关闭。
        2. 通过 CDP 下发 Network.setBlockedURLs，拦截 video.twimg.com 及 .mp4 / .m3u8 / .ts 视频切片。
        3. 配合 Playwright 路由层双重拦截兜底，在底层网络直接丢弃。
        4. 普通文字、头像、关注与点赞 100% 正常不受影响，单窗口内存立降至 200MB 左右，释放 60% 显存与 GPU 算力。
        """
        if not page:
            return
        if not getattr(config, "block_video_streams", True):
            return
        if getattr(page, "_video_blocking_applied", None) is True:
            return
        setattr(page, "_video_blocking_applied", True)

        blocked_patterns = [
            "*://video.twimg.com/*",
            "*.mp4*",
            "*.m3u8*",
            "*.ts*",
        ]

        # 1. CDP 协议层底层丢弃 (C++ 级别拦截，零 Python 性能开销)
        try:
            context = getattr(page, "context", None)
            if context and hasattr(context, "new_cdp_session"):
                cdp = await context.new_cdp_session(page)
                if hasattr(cdp, "send"):
                    await cdp.send("Network.enable")
                    await cdp.send("Network.setBlockedURLs", {"urls": blocked_patterns})
        except Exception as cdp_err:
            self.logger.debug("CDP 视频流阻断指令提示: %s", cdp_err)

        # 2. Playwright 路由层双重拦截兜底 (精准匹配视频 CDN 与多媒体分片)
        try:
            if hasattr(page, "route"):
                video_regex = re.compile(r"(video\.twimg\.com|\.(mp4|m3u8|ts)(\?|$))", re.IGNORECASE)

                async def _abort_video_request(route: Any) -> None:
                    try:
                        await route.abort()
                    except Exception:
                        pass

                await page.route(video_regex, _abort_video_request)
        except Exception as route_err:
            self.logger.debug("路由级视频流阻断提示: %s", route_err)

        self._print("⚡ [性能加速·极速省流] 已启用非必要大体积视频流阻断 (降低 60% 内存与显存占用，防多开卡屏)")
        self.logger.info("Video stream blocking active: video.twimg.com & *.mp4/*.m3u8/*.ts blocked via CDP & route")

    async def _ensure_on_track_and_clean_tabs(self, page: Any, current_keyword: str = "") -> bool:
        """多标签实时巡检与脱轨自愈：
        1. 检查 context.pages：智能白名单放行 X 页面，若发现非 X 外部标签页，立即秒关；
        2. 确保主推特工作页处于静默焦点状态 (CDP 焦点仿真，禁止 bring_to_front 抢占操作系统窗口)；
        3. 若主推特工作页自身 URL 意外脱轨离开了推特，自动执行 go_back 或重新拉回搜索页。
        """
        try:
            context = getattr(page, "context", None)
            # 1. 扫描并清理多余的非 X 外部标签页（放行用户自己打开的推特标签）
            if context and hasattr(context, "pages"):
                pages = list(getattr(context, "pages", []))
                for p in pages:
                    if p != page and not getattr(p, "is_closed", lambda: False)():
                        p_url = str(getattr(p, "url", "") or "").lower()
                        # X 域名白名单保护：只要是推特自身页面（x.com / twitter.com），绝对不关闭
                        if self._is_x_page_url(p_url) or p_url.startswith("devtools://") or p_url.startswith("chrome://"):
                            continue
                        try:
                            url_disp = (p_url[:50] + "...") if len(p_url) > 50 else p_url
                            self._print(f"🛡️ [标签清道] 发现多余外部标签页 ({url_disp})，已自动秒关")
                            await p.close()
                        except Exception:
                            pass

            # 2. 保持主推特页的 CDP 焦点仿真（静默后台运行，不抢占 OS 窗口及鼠标）
            if page and not getattr(page, "is_closed", lambda: False)():
                await self._ensure_page_focus_emulation(page)

            # 3. 检查主推特页自身是否脱轨离开推特
            current_url = str(getattr(page, "url", "") or "").lower()
            if current_url and not self._is_x_page_url(current_url) and not current_url.startswith("about:blank"):
                url_disp = (current_url[:60] + "...") if len(current_url) > 60 else current_url
                self._print(f"🔄 [脱轨自愈] 主页面意外偏离推特平台 ({url_disp})，正在执行紧急拉回...")
                back_ok = await self._safe_go_back(page)
                recheck_url = str(getattr(page, "url", "") or "").lower()
                if not back_ok or not self._is_x_page_url(recheck_url):
                    if current_keyword:
                        await self.navigate_to_keyword_search(page, current_keyword)
                    else:
                        await self._safe_goto(page, "https://x.com/home")
                return False
            return True
        except Exception as e:
            self.logger.debug("轨道巡检自愈非阻断提示: %s", e)
            return True

    async def _check_login_status(self, page: Any) -> bool:
        try:
            curr_url = getattr(page, "url", "").lower()
            if curr_url.startswith("chrome-error://"):
                return False
            if "/i/flow/login" in curr_url or "/login" in curr_url:
                return False

            post_btn = await page.query_selector('a[data-testid="SideNav_NewTweet_Button"]')
            profile_link = await page.query_selector('a[data-testid="AppTabBar_Profile_Link"]')
            account_switcher = await page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"]')

            if post_btn or profile_link or account_switcher:
                return True

            login_btn = await page.query_selector('a[data-testid="loginButton"]')
            if login_btn:
                return False

            if self._is_x_page_url(curr_url):
                signup_login = await page.query_selector('a[href*="/login"], a[href*="/i/flow/signup"]')
                if signup_login:
                    return False
        except Exception:
            pass
        return True

    async def _get_my_username(self, page: Any) -> str:
        if self.my_username:
            return self.my_username

        # 1. 优先从左下角账号切换器提取真实 @handle
        try:
            switcher = await page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"]')
            if switcher:
                text = await switcher.inner_text()
                match = re.search(r"@([A-Za-z0-9_]+)", text)
                if match:
                    self.my_username = match.group(1)
                    return self.my_username
        except Exception:
            pass

        # 2. 从侧边栏 Profile 链接提取
        try:
            profile_link = await page.query_selector('a[data-testid="AppTabBar_Profile_Link"]')
            if profile_link:
                href = await profile_link.get_attribute("href") or ""
                cand = href.strip("/").split("/")[0].split("?")[0]
                if cand and cand.lower() not in {"home", "explore", "notifications", "messages"}:
                    self.my_username = cand
                    return self.my_username
        except Exception:
            pass

        # 3. 从当前网页 URL 提取（仅当确认处于本人主页时，通过编辑主页按钮双重确权）
        try:
            has_edit_btn = bool(await page.query_selector('div[data-testid="editProfileButton"], a[href="/settings/profile"]'))
            if has_edit_btn:
                curr_url = getattr(page, "url", "") or ""
                match = re.search(r"x\.com/([A-Za-z0-9_]+)", curr_url)
                if match:
                    cand = match.group(1)
                    if cand.lower() not in {"home", "explore", "notifications", "messages", "settings", "i"}:
                        self.my_username = cand
                        return self.my_username
        except Exception:
            pass

        return ""


    async def _trigger_follow_limit_circuit_break(self, page: Any, detail: str = "") -> None:
        self._follow_limit_reached = True
        self._risk_reason = f"Follow Limit Exceeded: {detail}" if detail else "Follow Limit Exceeded"
        self._print(f"🚨 [风控限额感知] 发现平台关注上限并触发熔断: {detail or '已达平台关注上限'}")
        try:
            await self._capture_risk_snapshot(page, "follow_limit_toast")
        except Exception:
            pass
        window_name = (self._active_config.account_tag if self._active_config and self._active_config.account_tag and self._active_config.account_tag not in {"默认", ""} else "") or (self.tag if self.tag and self.tag not in {"系统", "默认"} else "") or self.tag or "默认窗口"
        handle = getattr(self, "my_username", "") or ""
        self._dispatch_risk_alert_telegram(
            window_name=window_name,
            handle=handle,
            err_code=141,
            err_msg=f"触发平台关注上限熔断: {detail or '已达平台关注上限'}",
            auto_pause=True,
        )
        raise RateLimitPause(1800, reason=self._risk_reason)

    async def _assert_no_challenge(self, page: Any) -> None:
        await self._check_run_control()
        await self._dismiss_modal_dialogs(page)
        if self._risk_reason:
            risk = self._risk_reason
            self._risk_reason = ""
            self._print(f"🚨 触发限流/风险阻断保护: {risk}")
            pause_dur = 1800 if any(k in risk for k in ["Limit", "RESTRICTED", "1800", "上限"]) else 900
            raise RateLimitPause(pause_dur, reason=risk)

        current_url = getattr(page, "url", "").lower()
        if any(kw in current_url for kw in ["account/access", "challenge", "captcha", "turnstile", "account/locked", "consent_flow"]):
            self._print(f"🚨 风控拦截！检测到人机验证/账号受限 URL: {current_url}")
            await self._capture_risk_snapshot(page, "challenge")
            raise CaptchaChallengeDetected(current_url)

        if "account/suspended" in current_url:
            self._print(f"🚨 账号封禁拦截！检测到账号冻结 URL: {current_url}")
            await self._capture_risk_snapshot(page, "suspended")
            raise AutomationEngineError(f"账号已被冻结 (Suspended): {current_url}")

        # 🛡️ 深度探针 1: 扫描页面全局 Toast 提示（捕获平台级关注上限与自动化特征警示）
        try:
            if hasattr(page, "query_selector"):
                toast = await page.query_selector('div[data-testid="toast"], [role="alert"]')
                if toast and hasattr(toast, "is_visible") and await toast.is_visible():
                    toast_text = (await toast.inner_text() or "").strip() if hasattr(toast, "inner_text") else ""
                    toast_lower = toast_text.lower()
                    if any(kw in toast_lower for kw in [
                        "unable to follow more", "これ以上のアカウントをフォローすることはできません", "无法关注更多用户",
                        "cannot follow", "limit reached", "关注上限", "フォローできません", "上限に達しました", "フォロー上限"
                    ]):
                        await self._trigger_follow_limit_circuit_break(page, toast_text)
                    elif any(kw in toast_lower for kw in [
                        "automated", "automated requests", "temporarily limited", "受限", "频次限制", "操作过于频繁",
                        "自動化", "一時的に制限", "制限されています", "不審なアクティビティ", "試行回数が多すぎます", "スパム"
                    ]):
                        self._print(f"🚨 [风控自动化拦截] 发现平台自动化告警 Toast: {toast_text}")
                        self._risk_reason = f"Toast Alert: {toast_text}"
                        await self._capture_risk_snapshot(page, "toast_risk")
                        raise RateLimitPause(900, reason=self._risk_reason)
        except RateLimitPause:
            raise
        except Exception:
            pass

        # 🛡️ 深度探针 2: 扫描受限/冻结模态弹窗 (SheetDialog / Modal)
        try:
            if hasattr(page, "query_selector"):
                modal_dialog = await page.query_selector('div[data-testid="sheetDialog"], div[aria-modal="true"], div[role="dialog"]')
                if modal_dialog and hasattr(modal_dialog, "is_visible") and await modal_dialog.is_visible():
                    dialog_text = (await modal_dialog.inner_text() or "").strip() if hasattr(modal_dialog, "inner_text") else ""
                    dialog_lower = dialog_text.lower()
                    if any(kw in dialog_lower for kw in [
                        "account is locked", "account suspended", "verify your phone", "verify your account", "suspend",
                        "账号已被锁定", "账号已被冻结", "受保护的账号",
                        "アカウントがロック", "アカウントはロック", "アカウントが凍結", "アカウントは凍結", "電話番号を認証", "アカウントの認証", "不審なログイン"
                    ]):
                        self._print(f"🚨 [账号锁定拦截] 发现平台受限模态弹窗: {dialog_text[:80]}...")
                        self._risk_reason = f"Modal Dialog Alert: {dialog_text[:80]}"
                        await self._capture_risk_snapshot(page, "modal_risk")
                        raise RateLimitPause(1800, reason=self._risk_reason)
        except RateLimitPause:
            raise
        except Exception:
            pass

        # 🛡️ 深度探针 3: 扫描 Cloudflare / Turnstile / Arkose Labs 挑战 iframe
        try:
            challenge_frame = await page.query_selector(
                'iframe[src*="challenges.cloudflare.com"], #challenge-stage, iframe[title*="Turnstile"], '
                'iframe[src*="arkoselabs.com"], iframe[src*="arkose"], div#FunCaptcha, div[id*="arkose"]'
            )
            if challenge_frame:
                self._print("🚨 风控拦截！DOM 结构中检测到人机验证组件 (Cloudflare / Arkose Labs)!")
                await self._capture_risk_snapshot(page, "challenge_component")
                raise CaptchaChallengeDetected(current_url)
        except CaptchaChallengeDetected:
            raise
        except Exception:
            pass

    async def _select_or_open_x_page(self, context: Any) -> Any:
        """智能获取或开启用于 X 自动化的页面。

        🛡️ 启动韧性与防碰撞设计：
        1. 启动等待与轮询：Chrome 刚被拉起时，首个主窗口与标签页在 CDP 中的注册通常有 0.3~2.5 秒延迟。
           若 context.pages 为空，自动轮询等待（最高 8 秒），避免在 Chrome 尚未就绪时盲目调用 context.new_page() 导致 Target.createTarget 报错；
        2. 清理残余与崩溃页：安全识别并过滤/关闭 chrome-error 标签页；
        3. 智能复用：优先复用已停留在 X / Twitter 的标签页；次选已有普通标签页（如空白页）并在当前页平滑导航至 X 首页；
        4. 防御式新建标签：若无可用标签页，带退避重试调用 context.new_page()，拦截 Target.createTarget / Failed to open a new tab 瞬时异常。
        """
        # 1. 智能等待初始页面注册就绪（最高 8 秒，绝大部分情况 0.5~1.5 秒即就绪）
        clean_pages = []
        poll_start = time.time()
        while time.time() - poll_start < 8.0:
            raw_pages = getattr(context, "pages", [])
            candidate_list = [p for p in raw_pages if not str(getattr(p, "url", "")).startswith("devtools")]

            clean_pages = []
            for p in candidate_list:
                is_closed_fn = getattr(p, "is_closed", None)
                if callable(is_closed_fn):
                    try:
                        if is_closed_fn() is True:
                            continue
                    except Exception:
                        pass
                p_url = str(getattr(p, "url", "") or "").lower()
                if p_url.startswith("chrome-error://"):
                    # 仅当有多个标签页时才关闭多余的错误残余页，若为唯一定义页则保留以防杀死整个浏览器
                    if len(candidate_list) > 1:
                        try:
                            await p.close()
                        except Exception:
                            pass
                        continue
                clean_pages.append(p)

            if clean_pages:
                break
            await asyncio.sleep(0.3)

        chosen_page = None
        # 2. 优先复用已经停留在 X / Twitter 域名的现有标签页
        for candidate in clean_pages:
            if self._is_x_page_url(getattr(candidate, "url", "")):
                chosen_page = candidate
                break

        # 3. 若现有标签页停留在 google.com、about:blank、chrome-error 等起始页，直接在当前标签页中导航至 X 首页
        if not chosen_page and clean_pages:
            candidate = clean_pages[0]
            try:
                self._print("🌐 当前标签页未在 X 首页，正在平滑导航至 https://x.com/home ...")
                await candidate.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25000)
                chosen_page = candidate
            except Exception as err:
                err_msg = str(err)
                if any(kw in err_msg for kw in ["ERR_PROXY", "ERR_INTERNET", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION", "ERR_TUNNEL", "ERR_TIMED_OUT"]):
                    self.logger.warning("代理网络未通 (导航 x.com 失败: %s)，保留当前页面并准备进入保活待机...", err)
                    chosen_page = candidate
                else:
                    self.logger.warning("导航现有页面至 x.com 异常: %s，准备尝试新建标签页...", err)

        # 4. 若无标签页或导航失败，带重试退避机制新建标签页
        if not chosen_page:
            last_err = None
            for attempt in range(1, 4):
                try:
                    if attempt > 1:
                        self._print(f"⏳ 正在重试新建标签页 (尝试 {attempt}/3)...")
                    chosen_page = await context.new_page()
                    if chosen_page:
                        break
                except Exception as np_err:
                    last_err = np_err
                    err_msg = str(np_err).lower()
                    if "createtarget" in err_msg or "failed to open a new tab" in err_msg or "protocol error" in err_msg:
                        self._print(f"⏳ 浏览器窗口就绪中，等待 1.2 秒后重试新建标签页 (尝试 {attempt}/3)...")
                        await asyncio.sleep(1.2)
                        # 检查在此期间是否有后台渲染完成的现有标签页
                        raw_pages = getattr(context, "pages", [])
                        valid_pages = [p for p in raw_pages if not str(getattr(p, "url", "")).startswith("devtools")]
                        if valid_pages:
                            chosen_page = valid_pages[0]
                            break
                    else:
                        raise np_err

            if not chosen_page and last_err:
                raise last_err

            if chosen_page:
                try:
                    self._print("🌐 正在新标签页中打开 https://x.com/home ...")
                    await chosen_page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25000)
                except Exception as err:
                    err_msg = str(err)
                    if any(kw in err_msg for kw in ["ERR_PROXY", "ERR_INTERNET", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION", "ERR_TUNNEL", "ERR_TIMED_OUT"]):
                        self.logger.warning("新标签页代理网络未通: %s，保留当前页面并准备进入保活待机...", err)
                    else:
                        self.logger.warning("新建页面并导航至 x.com 异常: %s", err)

        # 挂载网络响应、弹窗拒绝与 OOM 崩溃监听守卫
        if chosen_page:
            self._setup_page_listeners(chosen_page)
            self._is_page_crashed = False

            # 🛡️ 智能视口保障：始终沿用原生物理窗口渲染，杜绝调用 set_viewport_size 触发 DevTools 模拟黑边与指纹冲突
            try:
                cur_w = await chosen_page.evaluate("() => window.innerWidth")
                if cur_w and cur_w < 1080:
                    self.logger.debug("当前浏览器窗口较窄 (innerWidth=%s)，建议保持 >=1280 物理分辨率以防推特侧栏折叠", cur_w)
            except Exception:
                pass

        return chosen_page

    async def _wait_for_login_state_surface(self, page: Any, timeout_sec: float = 8.0) -> None:
        wait_for_selector = getattr(page, "wait_for_selector", None)
        if not callable(wait_for_selector):
            return
        selectors = (
            'a[data-testid="SideNav_NewTweet_Button"]',
            'a[data-testid="AppTabBar_Profile_Link"]',
            '[data-testid="SideNav_AccountSwitcher_Button"]',
            'div[data-testid="tweetTextarea_0"]',
            'a[href="/compose/post"]',
            'a[data-testid="loginButton"]',
            'a[href="/i/flow/login"]',
            'a[href="/login"]',
            'input[data-testid="SearchBox_Search_Input"]',
            'div[data-testid="primaryColumn"]',
        )
        try:
            await wait_for_selector(", ".join(selectors), state="attached", timeout=max(1, int(timeout_sec * 1000)))
        except Exception:
            pass

    async def _safe_goto(self, page: Any, url: str, referer: str = "https://x.com/home", timeout: int = 30000) -> bool:
        try:
            await page.goto(url, referer=referer, timeout=timeout, wait_until="domcontentloaded")
            try:
                if hasattr(page, "wait_for_load_state"):
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
            except Exception:
                pass
            net_err = await self._detect_network_proxy_error(page)
            if net_err:
                return await self._wait_for_network_recovery(page, initial_err=net_err)
            return True
        except Exception as err:
            err_str = str(err)
            if any(kw in err_str for kw in ["ERR_PROXY", "ERR_INTERNET", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION", "ERR_TUNNEL", "ERR_TIMED_OUT"]):
                return await self._wait_for_network_recovery(page, initial_err=err_str[:60])
            self.logger.debug("_safe_goto error: %s", err)
            return False

    async def _safe_go_back(self, page: Any) -> bool:
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=6000)
            await asyncio.sleep(random.uniform(1.5, 2.5))
            try:
                await page.wait_for_selector('article, div[data-testid="primaryColumn"]', timeout=4000)
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _scroll_with_control(self, page: Any, distance: int = 400) -> None:
        await human_discrete_scroll(page, distance=distance, sleep_func=self._sleep_with_control)
        self._scroll_actions_since_last_gc = getattr(self, "_scroll_actions_since_last_gc", 0) + 1
        if self._scroll_actions_since_last_gc >= 12:
            await self._maybe_purge_renderer_memory(page)

    async def _wait_for_page_ready(self, page: Any, timeout_sec: float = 10.0) -> bool:
        selectors = [
            'div[data-testid="primaryColumn"]',
            'article',
            'div[data-testid="cellInnerScrollbox"]',
            'input[data-testid="SearchBox_Search_Input"]',
            'nav[role="navigation"]'
        ]
        per_timeout = max(1000, int((timeout_sec * 1000) / len(selectors)))
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=per_timeout)
                if el:
                    return True
            except Exception:
                continue
        return False

    async def _dismiss_hover_card(self, page: Any) -> None:
        try:
            vp = getattr(page, "viewport_size", None) or {"width": 1280, "height": 800}
            w = vp.get("width", 1280) if isinstance(vp, dict) else 1280
            h = vp.get("height", 800) if isinstance(vp, dict) else 800
            neutral_x = random.randint(int(w * 0.12), int(w * 0.28))
            neutral_y = random.randint(int(h * 0.35), int(h * 0.65))
            await page.mouse.move(neutral_x, neutral_y)
            await asyncio.sleep(random.uniform(0.6, 1.2))
        except Exception:
            pass

    async def _detect_hover_card_with_human_dwell(self, page: Any) -> Any:
        hover_card = None
        max_attempts = random.randint(6, 8)
        for _ in range(max_attempts):
            await asyncio.sleep(0.35)
            hover_card = await page.query_selector(
                'div[data-testid="HoverCard"], '
                'div[id="layers"] div[data-testid="HoverCard"], '
                'div[id="layers"] div[role="dialog"]'
            )
            if hover_card:
                try:
                    if hasattr(hover_card, "is_visible") and not await hover_card.is_visible():
                        continue
                    box = await hover_card.bounding_box() if hasattr(hover_card, "bounding_box") else None
                    if box and box.get("width", 0) >= 40 and box.get("height", 0) >= 30:
                        dwell_focus = random.uniform(0.6, 1.2)
                        await asyncio.sleep(dwell_focus)
                        return hover_card
                except Exception:
                    pass
        return None

    async def _check_and_handle_retry(self, page: Any, current_keyword: str = "") -> bool:
        """检测推特网络抖动/请求频控错误重试卡片并拟人自愈：
        1. 智能匹配多语言错误状态 (出错了 / Something went wrong / 問題が発生しました) 与重试按钮 (重试 / Retry / やり直す)；
        2. 原生 DOM 标签注入定位，彻底解决 Twitter 动态无 data-testid 重试按钮匹配失败问题；
        3. 拟人停顿点击【重试】；若重试无效，自动梯次触发页面平滑重载 (Reload) 与搜索轨道重新注入 (Navigate)。
        """
        try:
            if not page or getattr(page, "is_closed", lambda: False)():
                return False

            # 1. 检测页面是否存在错误提示文本与重试按钮（通过注入评估避免类名/ID变动）
            check_script = """
            () => {
                const bodyText = document.body ? (document.body.innerText || "") : "";
                const errorPatterns = ["出错了", "重新加载", "Something went wrong", "Try reloading", "問題が発生しました", "やりなおしてください", "やり直してください", "Algo salió mal"];
                const hasErrorText = errorPatterns.some(p => bodyText.includes(p));

                const retryKeywords = ["重试", "retry", "try again", "やり直す", "再試行", "recargar", "réessayer"];
                const clickables = Array.from(document.querySelectorAll('button, div[role="button"], a[role="button"], [data-testid="empty_state_button_text"]'));
                
                let foundBtn = false;
                for (const el of clickables) {
                    const txt = (el.innerText || el.textContent || "").trim().toLowerCase();
                    if (retryKeywords.some(k => txt === k || txt.includes(k))) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 5 && rect.height > 5) {
                            const style = window.getComputedStyle(el);
                            if (style.visibility !== 'hidden' && style.display !== 'none' && parseFloat(style.opacity || '1') > 0.1) {
                                el.setAttribute('data-laogu-retry-btn', 'true');
                                foundBtn = true;
                                break;
                            }
                        }
                    }
                }
                return { hasErrorText, foundBtn };
            }
            """
            eval_res = {}
            if hasattr(page, "evaluate"):
                try:
                    eval_res = await page.evaluate(check_script) or {}
                except Exception:
                    eval_res = {}

            has_error = bool(eval_res.get("hasErrorText", False))
            found_btn_dom = bool(eval_res.get("foundBtn", False))

            btn = None
            if found_btn_dom:
                btn = await page.query_selector('[data-laogu-retry-btn="true"]')

            # 备用选择器池（覆盖原生与扩展结构）
            if not btn:
                fallback_selectors = [
                    'button:has-text("重试")',
                    'button:has-text("Retry")',
                    'button:has-text("Try again")',
                    'button:has-text("やり直す")',
                    'button:has-text("再試行")',
                    'div[role="button"]:has-text("重试")',
                    'div[role="button"]:has-text("Retry")',
                    'div[role="button"]:has-text("やり直す")',
                    'button[data-testid="empty_state_button_text"]',
                    'div[data-testid="error-detail"] button',
                    'div[data-testid="emptyState"] button',
                ]
                for sel in fallback_selectors:
                    try:
                        candidate = await page.query_selector(sel)
                        if candidate:
                            btn = candidate
                            break
                    except Exception:
                        pass

            # 若未检测到错误文本且未找到重试按钮，直接返回正常
            if not has_error and not btn:
                return False

            self._print("⏳ 检测到推特接口请求异常 (出错了。请尝试重新加载 / Retry)，拟人缓冲 3 秒准备自愈...")
            await asyncio.sleep(random.uniform(2.5, 4.0))

            # 阶段 1：尝试拟人点击【重试】按钮
            if btn:
                self._print("🔄 尝试拟人点击【重试】按钮恢复页面推文流...")
                try:
                    click_ok = await safe_human_click(page, btn, self.personality)
                    if not click_ok and hasattr(btn, "click"):
                        await btn.click(timeout=3000)
                except Exception as click_err:
                    self.logger.debug("点击重试按钮异常: %s", click_err)

            await asyncio.sleep(random.uniform(3.0, 4.5))

            # 验证点击重试后推文是否恢复
            try:
                articles = await page.query_selector_all("article")
                if articles:
                    self._print(f"✅ 【重试】生效，推文流已成功恢复 (检测到 {len(articles)} 条推文)！")
                    return True
            except Exception:
                pass

            # 阶段 2：若点击重试后仍处于错误状态，执行平滑刷新 (Reload)
            self._print("⚠️ 点击【重试】后推文流未加载，静眠 8 秒避开频控后执行页面平滑刷新自愈...")
            await self._sleep_with_control(random.uniform(6.0, 9.0))
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(random.uniform(3.5, 5.0))
                articles = await page.query_selector_all("article")
                if articles:
                    self._print(f"✅ 页面平滑刷新成功，推文流已恢复 (检测到 {len(articles)} 条推文)！")
                    return True
            except Exception as reload_err:
                self.logger.debug("重载页面异常: %s", reload_err)

            # 阶段 3：若刷新后仍未恢复，重新注入关键词检索/首页推荐轨道
            kw_disp = f"关键词搜索轨道: '{current_keyword}'" if current_keyword else "X 首页推荐流轨道"
            self._print(f"🔄 页面刷新后仍存在异常，重新导航拉回{kw_disp} ...")
            try:
                await self.navigate_to_keyword_search(page, current_keyword)
                await asyncio.sleep(random.uniform(3.0, 4.5))
                articles = await page.query_selector_all("article")
                if articles:
                    self._print("✅ 重新导航成功，推特推文流已完全恢复正常！")
                    return True
            except Exception as nav_err:
                self.logger.debug("重新导航异常: %s", nav_err)

            return False
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self.logger.debug("检测与处理重试异常: %s", e)
            return False

    @staticmethod
    async def _is_promoted_tweet(article: Any) -> bool:
        """识别推特官方商业赞助/推广推文 (Promoted Tweet)"""
        try:
            if await article.query_selector('[data-testid="placementTracking"], svg[data-testid="icon-promoted"]'):
                return True
            art_text = (await article.inner_text() or "").lower()
            if any(w in art_text for w in ["promoted", "プロモーション", "赞助", "广告"]):
                return True
        except Exception:
            pass
        return False

    @staticmethod
    async def _pre_click_card_guard(article: Any, handle: str, config: Optional[AutomationConfig] = None) -> tuple[bool, str]:
        """在鼠标点击进主页前，快速在推文卡片层质检默认头像、纯数字乱码号及低质营销推文。
        返回 (is_bad, reason)。
        """
        try:
            has_long_digits = bool(re.search(r"\d{7,}$", handle))
            smart_newbie_enabled = getattr(config, "smart_newbie_recognition_enabled", True) if config else True

            # 检查头像是否为默认头像 (Default Avatar / 蛋头 / 灰色剪影)
            is_default_avatar = False
            avatar_el = await article.query_selector('div[data-testid="Tweet-User-Avatar"] img, img[src*="profile_images"], img[src*="default_profile"]')
            if not avatar_el:
                avatar_el = await article.query_selector('div[data-testid="Tweet-User-Avatar"]')
            if avatar_el:
                src = await avatar_el.get_attribute("src") or ""
                if not src and hasattr(avatar_el, "query_selector"):
                    nested_img = await avatar_el.query_selector("img")
                    if nested_img:
                        src = await nested_img.get_attribute("src") or ""
                if "default_profile" in src.lower() or "silhouette" in src.lower():
                    is_default_avatar = True

            # 检查推文是否纯外链且无实质文字内容 (常见发推广告机)
            card_text = (await article.inner_text() or "").strip()
            lines = [l.strip() for l in card_text.split("\n") if l.strip()]
            is_pure_ad_link = len(lines) <= 2 and any("t.co" in l or "http" in l for l in lines)

            # 1. 头像本身就是默认初始头像
            if is_default_avatar:
                if has_long_digits:
                    return True, f"用户名 @{handle} 含多位数字且为默认初始头像 (高疑似批量僵尸小号)"
                return True, f"@{handle} 使用默认初始头像 (低活跃度/死号特征)"

            # 2. 推文是纯外链广告
            if is_pure_ad_link:
                if has_long_digits:
                    return True, f"用户名 @{handle} 含多位数字且为纯广告外链推文 (高疑似发帖机器人)"
                return True, "推文仅包含外部推广链接无正文 (推广机器人)"

            # 3. 针对末尾连续 7 位以上数字后缀的用户名处理
            if has_long_digits:
                if smart_newbie_enabled:
                    # 开启新人智能识别：博主已更换个性生活头像且推文为正常原创内容 -> 判定为初级真实新手素人，放行准入！
                    return False, ""
                else:
                    # 未开启新人智能识别：执行严格排斥规则，直接一票否决
                    return True, f"用户名 @{handle} 含超长连续数字后缀 (机器人特征)"

            return False, ""
        except Exception:
            return False, ""


    async def _execute_natural_roaming(self, page: Any, config: AutomationConfig) -> int:
        """自然摸鱼与行为噪声注入：随机切回 X 首页推荐流摸鱼闲逛，打破单一搜索路径指纹。"""
        if not getattr(config, "natural_roaming_enabled", True):
            return 0
        self._print("🛋️ [自然摸鱼] 注入行为噪声：随机访问首页推荐流摸鱼闲逛 15~25 秒，打乱纯搜索行为指纹...")
        try:
            likes_added = await self._browse_home_feed(page, config)
            self._print("✅ [自然摸鱼] 首页闲逛结束，平滑拉回目标搜索轨道...")
            if config.keyword:
                await self.navigate_to_keyword_search(page, config.keyword)
                await asyncio.sleep(random.uniform(2.5, 4.0))
            return likes_added
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self.logger.debug("自然摸鱼执行异常: %s", e)
            return 0

    def _dispatch_risk_alert_telegram(
        self,
        window_name: str,
        handle: str,
        err_code: int | str,
        err_msg: str,
        auto_pause: bool,
    ) -> None:
        """异步第一时间向 Telegram 推送推特风控拦截报警 (附带窗口名称与账号ID)."""
        try:
            now = time.time()
            dedup_key = (str(err_code), str(window_name))
            if not hasattr(self, "_last_risk_alert_time"):
                self._last_risk_alert_time = {}
            if now - self._last_risk_alert_time.get(dedup_key, 0) < 60:
                return
            self._last_risk_alert_time[dedup_key] = now

            def _send():
                try:
                    notifier = TelegramNotifier()
                    notifier.send_risk_alert(
                        window_name=window_name,
                        handle=handle,
                        error_code=err_code,
                        error_message=err_msg,
                        auto_paused=auto_pause,
                    )
                except Exception as ex:
                    self.logger.warning("推特风控 Telegram 告警推送失败: %s", ex)

            threading.Thread(target=_send, daemon=True).start()
        except Exception:
            pass

    def _dispatch_network_alert_telegram(
        self,
        window_name: str,
        handle: str,
        err_msg: str,
    ) -> None:
        """异步第一时间向 Telegram 推送代理网络断开/保活待机报警 (附带窗口名称与账号ID，带 60 秒防刷)."""
        try:
            now = time.time()
            dedup_key = ("NET_OFFLINE", str(window_name))
            if not hasattr(self, "_last_risk_alert_time"):
                self._last_risk_alert_time = {}
            if now - self._last_risk_alert_time.get(dedup_key, 0) < 60:
                return
            self._last_risk_alert_time[dedup_key] = now

            def _send():
                try:
                    notifier = TelegramNotifier()
                    notifier.send_network_alert(
                        window_name=window_name,
                        handle=handle,
                        error_message=err_msg,
                    )
                except Exception as ex:
                    self.logger.warning("代理断开 Telegram 告警推送失败: %s", ex)

            threading.Thread(target=_send, daemon=True).start()
        except Exception:
            pass

    async def _detect_network_proxy_error(self, page: Any) -> str:
        """探测当前页面是否处于网络不通/代理断网/无法访问状态。

        返回具体的网络错误描述（如 "ERR_PROXY_CONNECTION_FAILED"），若网络正常则返回空字符串 ""。
        """
        try:
            if not page or getattr(page, "is_closed", lambda: False)():
                return ""
            curr_url = str(getattr(page, "url", "") or "").lower()
            if curr_url.startswith("chrome-error://"):
                try:
                    err_text = await page.evaluate("""() => {
                        const body = document.body ? document.body.innerText : '';
                        const match = body.match(/ERR_[A-Z0-9_]+/);
                        return match ? match[0] : '';
                    }""")
                    if err_text:
                        return err_text
                except Exception:
                    pass
                return "ERR_PROXY_CONNECTION_FAILED"

            try:
                body_sample = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 400) : ''")
                net_err_keywords = [
                    "ERR_PROXY_CONNECTION_FAILED",
                    "ERR_INTERNET_DISCONNECTED",
                    "ERR_NAME_NOT_RESOLVED",
                    "ERR_CONNECTION_REFUSED",
                    "ERR_TUNNEL_CONNECTION_FAILED",
                    "ERR_TIMED_OUT",
                    "ERR_CONNECTION_CLOSED",
                    "ERR_NETWORK_CHANGED",
                    "无法访问此网站",
                    "未连接到互联网",
                    "代理服务器出现问题",
                    "这个网站无法访问",
                    "このサイトにアクセスできません",
                    "インターネットに接続されていません",
                    "プロキシ サーバーに問題があります",
                    "This site can't be reached",
                    "No internet",
                    "There is no Internet connection",
                ]
                for kw in net_err_keywords:
                    if kw in body_sample:
                        return kw
            except Exception:
                pass
        except Exception:
            pass
        return ""

    async def _wait_for_network_recovery(self, page: Any, initial_err: str = "") -> bool:
        """当代理断网或无网络时，保持浏览器开启并在后台轮询等待网络恢复，绝不强退。"""
        err_display = initial_err or "ERR_PROXY_CONNECTION_FAILED"
        self._print(f"🌐 ⚠️ [网络连通性异常] 检测到浏览器代理网络未连通 ({err_display})！")
        self._print("💡 [保活待机保护] 控制中心已开启【断网不退出保护】：浏览器已保持开启，引擎正在后台守候网络恢复...")
        self._print("👉 请检查您的代理软件（如 Clash / v2ray / 老谷代理池）节点连通性。一旦网络恢复，引擎将立即自动恢复执行！")

        window_name = (self._active_config.account_tag if self._active_config and self._active_config.account_tag and self._active_config.account_tag not in {"默认", ""} else "") or (self.tag if self.tag and self.tag not in {"系统", "默认"} else "") or self.tag or "默认窗口"
        handle = getattr(self, "my_username", "") or ""
        self._dispatch_network_alert_telegram(window_name, handle, err_display)

        last_prompt_time = time.time()
        while True:
            await self._check_run_control()

            if not page or getattr(page, "is_closed", lambda: False)():
                return False

            try:
                await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1.0)
                net_err = await self._detect_network_proxy_error(page)
                if not net_err:
                    self._print("✅ 🌐 [网络已恢复] 探测到代理网络已恢复连通！正在恢复自动化拓客任务...")
                    return True
            except Exception as e:
                err_s = str(e).lower()
                if "target closed" in err_s or "browser has been closed" in err_s:
                    raise

            now = time.time()
            if now - last_prompt_time >= 30.0:
                self._print("⏳ [后台守候中] 代理网络尚未恢复，持续保活待机中... (如需结束任务可在面板点击【停止】)")
                last_prompt_time = now

            await asyncio.sleep(6.0)

    async def _handle_response_interception(self, response: Any) -> None:
        """带 Content-Type 校验的轻量化 JSON 抓包截获器与风控主动感知"""
        try:
            url = getattr(response, "url", "")
            status = getattr(response, "status", 200)

            # 仅处理 Twitter / X 核心接口请求，过滤掉第三方遥测、CDN、广告等噪音
            if not any(domain in url for domain in ["x.com/i/api/", "api.twitter.com", "api.x.com"]):
                return

            if status == 429:
                # 排除非核心的后台小红点/未读数轮询接口 (ViewerBadgeCounts 等前端非致命遥测)
                if any(ign in url for ign in ["ViewerBadgeCounts", "BadgeCount", "useFetch", "QuickPromote"]):
                    self.logger.debug("忽略非核心后台遥测接口 429: %s", url[:80])
                    return
                # 仅针对推特核心拓客与写操作接口被 429 限流时才进行风控熔断:
                core_apis = ["CreateFriendship", "FavoriteTweet", "CreateTweet", "SearchTimeline", "UserTweets", "UserBy"]
                if any(api_kw in url for api_kw in core_apis):
                    window_name = (self._active_config.account_tag if self._active_config and self._active_config.account_tag and self._active_config.account_tag not in {"默认", ""} else "") or (self.tag if self.tag and self.tag not in {"系统", "默认"} else "") or self.tag or "默认窗口"
                    handle = getattr(self, "my_username", "") or ""
                    auto_pause = getattr(self._active_config, "graphql_risk_pause_enabled", True) if self._active_config else True
                    if auto_pause:
                        self._print(f"🚨 [HTTP 429] 监测到推特核心接口请求被限制 (Too Many Requests): {url[:80]}")
                        self._risk_reason = "HTTP_429_RATE_LIMITED"
                    else:
                        self._print(f"⚠️ [HTTP 429·用户已关闭熔断] 监测到推特接口被限制 (Too Many Requests): {url[:80]} (任务继续推进)")
                    self._dispatch_risk_alert_telegram(
                        window_name=window_name,
                        handle=handle,
                        err_code=429,
                        err_msg="HTTP 429 Too Many Requests: 推特核心请求频次过高被限流",
                        auto_pause=auto_pause,
                    )
                return

            # 针对核心写操作 (关注 CreateFriendship、发推/评论 CreateTweet) 进行 403 连续失败熔断判定
            is_write_mutation = any(mut in url for mut in ["CreateFriendship", "CreateTweet"])
            if is_write_mutation:
                if status == 200:
                    self._consecutive_write_403_count = 0
                elif status == 403:
                    self._consecutive_write_403_count = getattr(self, "_consecutive_write_403_count", 0) + 1
                    self.logger.warning("推特核心写操作返回 403 (连续第 %d 次): %s", self._consecutive_write_403_count, url[:80])
                    if self._consecutive_write_403_count >= 3:
                        self._print("🚨 [风控熔断] 连续 3 次写操作遭遇 HTTP 403 拒绝，账号已被平台限制写入权限！触发安全熔断挂起。")
                        self._risk_reason = "HTTP_403_WRITE_RESTRICTED"
                        window_name = (self._active_config.account_tag if self._active_config and self._active_config.account_tag and self._active_config.account_tag not in {"默认", ""} else "") or (self.tag if self.tag and self.tag not in {"系统", "默认"} else "") or self.tag or "默认窗口"
                        handle = getattr(self, "my_username", "") or ""
                        self._dispatch_risk_alert_telegram(
                            window_name=window_name,
                            handle=handle,
                            err_code=403,
                            err_msg="HTTP 403: 连续3次写操作被拒，账号已遭写权限限制",
                            auto_pause=True,
                        )

            # 注意：HTTP 401/403 在访问私密博主 (锁推)、未公开关注列表或受保护内容时极其普遍，
            # 绝不能将其单次判定为我方账号的风控阻断，否则会导致拓客被误杀中断。
            if status in {401, 403}:
                if any(mut in url for mut in ["CreateFriendship", "FavoriteTweet", "CreateTweet"]):
                    self.logger.debug("写操作返回 %s: %s", status, url[:80])
                return

            headers = getattr(response, "headers", {}) or {}
            content_type = ""
            if isinstance(headers, dict):
                for hk, hv in headers.items():
                    if str(hk).lower() == "content-type":
                        content_type = str(hv).lower()
                        break
            if "application/json" not in content_type:
                return

            if any(k in url for k in ["UserBy", "HoverCard", "UserDetail", "Viewer", "ProfileSpotlight", "graphql", "SearchTimeline", "search", "adaptive", "Create"]):
                if status == 200:
                    json_data = await response.json()
                    # 检查推特 GraphQL 返回的业务风控错误码
                    errors = json_data.get("errors")
                    if isinstance(errors, list) and errors:
                        for err_item in errors:
                            if isinstance(err_item, dict):
                                err_code = err_item.get("code")
                                err_msg = str(err_item.get("message") or "")
                                # 仅当拦截到明确属于我方账号维度的致命风控码时才触发主动挂起:
                                # 326: 账号被风控锁定 (需验证码/解封)
                                # 64: 账号冻结
                                # 226: 判定为自动化行为 (Automated request detected)
                                # 399: 触发人机验证挑战 (Challenge required)
                                # 141: 关注频次达单日硬限制
                                # 185: 单日推文/状态发布达上限 (Daily status update limit)
                                if err_code in {326, 64, 226, 399, 141, 185}:
                                    window_name = (self._active_config.account_tag if self._active_config and self._active_config.account_tag and self._active_config.account_tag not in {"默认", ""} else "") or (self.tag if self.tag and self.tag not in {"系统", "默认"} else "") or self.tag or "默认窗口"
                                    handle = getattr(self, "my_username", "") or ""
                                    handle_display = f"@{handle}" if handle else "未抓取"
                                    auto_pause = getattr(self._active_config, "graphql_risk_pause_enabled", True) if self._active_config else True

                                    # 1. 默认控制台/日志提醒
                                    if auto_pause:
                                        self._print(f"🚨 [推特风控熔断拦截] 窗口: [{window_name}] | 账号: {handle_display} | 触发风控限制 (Code {err_code}): {err_msg}")
                                    else:
                                        self._print(f"⚠️ [推特风控拦截·用户已关闭熔断] 窗口: [{window_name}] | 账号: {handle_display} | 触发风控限制 (Code {err_code}): {err_msg} (任务继续推进)")

                                    # 2. 第一时间在 Telegram 机器人发送指定窗口名称与账号ID的警报
                                    self._dispatch_risk_alert_telegram(
                                        window_name=window_name,
                                        handle=handle,
                                        err_code=err_code,
                                        err_msg=err_msg,
                                        auto_pause=auto_pause,
                                    )

                                    # 3. 若开启自动熔断，则挂起任务；若用户关闭，则放行
                                    if auto_pause:
                                        if err_code in {326, 64, 226, 399}:
                                            self._risk_reason = f"GRAPHQL_ERROR_{err_code}"
                                        else:
                                            self._risk_reason = f"LIMIT_EXCEEDED_{err_code}"
                                        return

                    user_data = json_data.get("data", {}).get("user", {}).get("result", {}) or json_data.get("data", {}).get("viewer", {})
                    if isinstance(user_data, dict) and user_data:
                        u_info = self._extract_single_user_info(user_data)
                        if u_info and u_info.get("screen_name"):
                            sn = u_info["screen_name"].lower()
                            self.user_cache[sn] = {
                                "followers_count": u_info.get("followers_count", -1),
                                "friends_count": u_info.get("friends_count", -1),
                                "statuses_count": u_info.get("statuses_count", -1),
                                "description": u_info.get("description", ""),
                                "name": u_info.get("name", ""),
                                "verified": u_info.get("verified", False),
                                "protected": u_info.get("protected", False),
                            }

                    # ⚡ 底层数据流极速透视与博主资料池秒级刷新
                    self._parse_and_cache_scout_candidates(json_data, self._active_config)
        except Exception:
            pass

    @staticmethod
    def _extract_single_user_info(user_dict: Any) -> dict[str, Any] | None:
        """从推特 GraphQL 的 User 节点中统合提取指标，同时兼容 2026 最新核心架构与 Legacy 传统字段."""
        if not isinstance(user_dict, dict):
            return None
        if user_dict.get("__typename") == "UserWithVisibilityResults" and isinstance(user_dict.get("user"), dict):
            user_dict = user_dict["user"]

        core = user_dict.get("core", {}) if isinstance(user_dict.get("core"), dict) else {}
        legacy = user_dict.get("legacy", {}) if isinstance(user_dict.get("legacy"), dict) else {}
        rel_counts = user_dict.get("relationship_counts", {}) if isinstance(user_dict.get("relationship_counts"), dict) else {}
        rel_perspectives = user_dict.get("relationship_perspectives", {}) if isinstance(user_dict.get("relationship_perspectives"), dict) else {}
        bio_obj = user_dict.get("profile_bio", {}) if isinstance(user_dict.get("profile_bio"), dict) else {}
        avatar_obj = user_dict.get("avatar", {}) if isinstance(user_dict.get("avatar"), dict) else {}
        tweet_counts = user_dict.get("tweet_counts", {}) if isinstance(user_dict.get("tweet_counts"), dict) else {}
        verification = user_dict.get("verification", {}) if isinstance(user_dict.get("verification"), dict) else {}
        privacy = user_dict.get("privacy", {}) if isinstance(user_dict.get("privacy"), dict) else {}

        sn = core.get("screen_name") or legacy.get("screen_name")
        if not sn:
            return None
        sn_norm = str(sn).strip().lstrip("@")
        name = core.get("name") or legacy.get("name", "")
        desc = bio_obj.get("description") or legacy.get("description", "")
        followers = rel_counts.get("followers") if "followers" in rel_counts else legacy.get("followers_count", -1)
        friends = rel_counts.get("following") if "following" in rel_counts else legacy.get("friends_count", -1)
        statuses = tweet_counts.get("tweets") if "tweets" in tweet_counts else legacy.get("statuses_count", -1)
        avatar_url = avatar_obj.get("image_url", "") or legacy.get("profile_image_url_https", "")
        default_avatar = bool(legacy.get("default_profile_image", False) or "default_profile" in avatar_url)
        verified = bool(verification.get("verified") or user_dict.get("is_blue_verified") or legacy.get("verified"))
        following = bool(rel_perspectives.get("following") or legacy.get("following", False))
        followed_by = bool(rel_perspectives.get("followed_by") or legacy.get("followed_by", False))
        protected = bool(privacy.get("protected") or legacy.get("protected", False))
        created_at = core.get("created_at") or legacy.get("created_at", "")

        return {
            "screen_name": sn_norm,
            "name": str(name).strip(),
            "description": str(desc).strip(),
            "followers_count": followers if isinstance(followers, int) else -1,
            "friends_count": friends if isinstance(friends, int) else -1,
            "statuses_count": statuses if isinstance(statuses, int) else -1,
            "created_at": str(created_at).strip(),
            "default_profile_image": default_avatar,
            "following": following,
            "followed_by": followed_by,
            "verified": verified,
            "protected": protected,
        }

    def _extract_scout_items_from_json(self, data: Any) -> list[dict[str, Any]]:
        """从推特 GraphQL/API 响应数据中提取推文与博主元数据 (兼容 2026 最新架构与 Legacy)."""
        items: list[dict[str, Any]] = []
        seen_handles: set[str] = set()

        def _walk(obj: Any) -> None:
            if isinstance(obj, dict):
                # 1. 优先提取带有推文上下文的博主节点 (Tweet / TweetWithVisibilityResults)
                if "tweet_results" in obj and isinstance(obj["tweet_results"], dict):
                    res = obj["tweet_results"].get("result")
                    if isinstance(res, dict):
                        tweet_obj = res.get("tweet", res) if res.get("__typename") == "TweetWithVisibilityResults" else res
                        user_res = (
                            tweet_obj.get("core", {})
                            .get("user_results", {})
                            .get("result", {})
                        )
                        u_info = self._extract_single_user_info(user_res)
                        if u_info:
                            sn_lower = u_info["screen_name"].lower()
                            if sn_lower not in seen_handles:
                                seen_handles.add(sn_lower)
                                tweet_legacy = tweet_obj.get("legacy", {}) if isinstance(tweet_obj.get("legacy"), dict) else {}
                                u_info["tweet_text"] = str(tweet_legacy.get("full_text", "")).strip()
                                u_info["favorite_count"] = tweet_legacy.get("favorite_count", 0)
                                u_info["retweet_count"] = tweet_legacy.get("retweet_count", 0)
                                u_info["reply_count"] = tweet_legacy.get("reply_count", 0)
                                items.append(u_info)

                # 2. 独立 User 节点 (如 Followers/Following/UserCell 等列表)
                elif obj.get("__typename") == "User":
                    u_info = self._extract_single_user_info(obj)
                    if u_info:
                        sn_lower = u_info["screen_name"].lower()
                        if sn_lower not in seen_handles:
                            seen_handles.add(sn_lower)
                            u_info["tweet_text"] = ""
                            u_info["favorite_count"] = 0
                            u_info["retweet_count"] = 0
                            u_info["reply_count"] = 0
                            items.append(u_info)

                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        _walk(v)
            elif isinstance(obj, list):
                for it in obj:
                    if isinstance(it, (dict, list)):
                        _walk(it)

        _walk(data)
        return items

    def _evaluate_scout_candidate(self, cand: dict[str, Any], config: AutomationConfig) -> tuple[bool, str]:
        """对底层 GraphQL 透视提取的博主进行内存秒级质检预筛 (0.001 秒决断)."""
        handle = cand.get("screen_name", "")
        handle_lower = handle.lower()
        name = cand.get("name", "")
        bio = cand.get("description", "")
        tweet_text = cand.get("tweet_text", "")
        followers = cand.get("followers_count", -1)
        friends = cand.get("friends_count", -1)
        statuses = cand.get("statuses_count", -1)
        following = cand.get("following", False)
        is_verified = cand.get("verified", False)
        is_protected = cand.get("protected", False)
        default_avatar = cand.get("default_profile_image", False)

        # 0. 排除自身账号
        if handle_lower and handle_lower == getattr(self, "my_username", "").lower():
            return False, "当前操作者自身账号"

        # 1. 自身已关注博主直接跳过
        if following:
            return False, "我方账号当前已在关注中"

        # 2. 私密/锁推账号跳过
        if is_protected:
            return False, "私密/锁推账号 (关注需人工审批)"

        # 3. 默认初始头像检测 (水军/机器人)
        if default_avatar and getattr(config, "filter_bot_accounts", True):
            return False, "默认初始头像 (高疑似幽灵/水军号)"

        # 4. 粉丝门槛质检
        if isinstance(followers, int) and followers >= 0:
            if config.max_follower_threshold > 0 and followers > config.max_follower_threshold:
                return False, f"粉丝数 ({followers}) 超出预设门槛 ({config.max_follower_threshold})"

        # 0粉丝0关注纯新号/僵尸号
        if followers == 0 and friends == 0 and getattr(config, "filter_bot_accounts", True):
            return False, "0粉丝0关注 (零活跃幽灵号)"

        # 发帖量质检
        if isinstance(statuses, int) and statuses > config.max_statuses_threshold and config.max_statuses_threshold > 0 and statuses != -1:
            return False, f"发帖量 ({statuses}) 超出预设门槛 ({config.max_statuses_threshold})"

        # 5. 认证号 (蓝勾/金勾) 过滤
        if getattr(config, "filter_verified_accounts", True) and is_verified:
            return False, "已认证账号 (蓝勾/金勾/官方账号)"

        # 6. 黑名单敏感词 & 仿冒/机器人/Spam 综合过滤
        combined_text = f"{name} {handle} {bio} {tweet_text}".strip()
        is_filtered, filter_reason = AccountFilterGuard.check_candidate_account(
            name=name,
            handle=handle,
            has_verified_badge=is_verified,
            card_or_dom_text=combined_text,
            config=config,
        )
        if is_filtered:
            return False, filter_reason

        # 7. 30天持久化历史库查重
        if getattr(self, "history_pool", None) and self.history_pool.is_visited(self.tag, handle_lower):
            return False, "近30天内已处理/互动过 (历史记忆去重)"

        return True, "优质准入目标"

    def _parse_and_cache_scout_candidates(self, json_data: dict, config: AutomationConfig | None = None) -> int:
        """解析并缓存底层数据流中的博主，若开启透视预筛则在内存中完成秒级质检."""
        try:
            items = self._extract_scout_items_from_json(json_data)
            if not items:
                return 0

            cfg = config or self._active_config
            scout_enabled = bool(cfg and getattr(cfg, "graphql_scout_filter_enabled", True))

            new_qualified = 0
            new_rejected = 0
            for item in items:
                handle = item.get("screen_name", "")
                if not handle:
                    continue
                handle_lower = handle.lower()

                # 始终保持全量 user_cache 刷新，加速任意模式下的 followers 查询
                followers_count = item.get("followers_count", -1)
                friends_count = item.get("friends_count", -1)
                statuses_count = item.get("statuses_count", -1)
                self.user_cache[handle_lower] = {
                    "followers_count": followers_count if isinstance(followers_count, int) else -1,
                    "friends_count": friends_count if isinstance(friends_count, int) else -1,
                    "statuses_count": statuses_count if isinstance(statuses_count, int) else -1,
                    "description": item.get("description", ""),
                    "name": item.get("name", ""),
                    "verified": item.get("verified", False),
                    "protected": item.get("protected", False),
                }

                # 若开启底层透视预筛：毫秒级质检并分流至候选池或淘汰库
                if scout_enabled and cfg:
                    passed, reason = self._evaluate_scout_candidate(item, cfg)
                    if passed:
                        self._scout_candidates[handle_lower] = item
                        new_qualified += 1
                        self.logger.debug("[底层透视] 发现合格博主 @%s: 粉丝=%s, 关注=%s", handle, followers_count, friends_count)
                    else:
                        self._scout_rejected_handles.add(handle_lower)
                        self._scout_rejection_reasons[handle_lower] = reason
                        new_rejected += 1
                        self.logger.debug("[底层透视] 质检淘汰博主 @%s: %s", handle, reason)

            if scout_enabled and (new_qualified > 0 or new_rejected > 0):
                self._print(
                    f"⚡ [底层透视·数据流拦截] 实时拦截并透视底层数据流: 解析 {len(items)} 位博主 (准入合格: {new_qualified} 位 | 秒级淘汰: {new_rejected} 位)"
                )
            elif not scout_enabled and len(items) > 0:
                self.logger.debug("已将底层数据流中的 %s 位博主指标同步至内存缓存 (加速名片质检)", len(items))

            return len(items)
        except Exception as e:
            self.logger.debug("解析底层数据流透视候选异常: %s", e)
            return 0

    async def _clean_unreciprocated_follows(self, page: Any, config: AutomationConfig, max_unfollow: int = 5) -> int:
        """扫描自身关注列表，优雅取关超期未回关的博主以维护关注/粉丝比 (支持多屏滚动分页巡检与弹窗安全闭环)."""
        if getattr(config, "dry_run", False):
            self._print("🔎 预演模式：跳过自动清理未回关博主动作")
            return 0
        if not config.smart_unfollow_enabled:
            return 0
        if getattr(self, "_cleaned_unreciprocated_today", False):
            return 0

        account_key = self.tag or config.account_tag or "default"
        old_handles = self.history_pool.get_handles_older_than(account_key, config.unfollow_threshold_days)
        if not old_handles:
            self._print(f"🧹 [比例维护] 当前暂无关注超过 {config.unfollow_threshold_days} 天的博主，跳过清理")
            return 0

        self._print(f"🧹 [比例维护] 启动关注比例健康检查：检测到 {len(old_handles)} 位关注超 {config.unfollow_threshold_days} 天的目标，开始巡检回关状态...")
        unfollowed_count = 0
        scanned_handles: set[str] = set()
        max_scroll_pages = 4  # 最多下滚 4 屏深度扫描

        try:
            if not self.my_username:
                self.my_username = await self._get_my_username(page)
            if not self.my_username:
                return 0

            following_url = f"https://x.com/{self.my_username}/following"
            curr_url_lower = str(getattr(page, "url", "") or "").lower()
            if f"/{self.my_username.lower()}/following" not in curr_url_lower:
                await self._safe_goto(page, following_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3.0)

            for scroll_page in range(max_scroll_pages):
                if unfollowed_count >= max_unfollow:
                    break

                user_cells = await page.query_selector_all('div[data-testid="UserCell"]')
                if not user_cells:
                    break

                new_cells_in_page = 0
                for cell in user_cells:
                    if unfollowed_count >= max_unfollow:
                        break

                    try:
                        link = await cell.query_selector('a[href^="/"]')
                        if not link:
                            continue
                        href = await link.get_attribute("href") or ""
                        handle = href.strip("/").split("/")[0].split("?")[0].lower()
                        if not handle or handle == self.my_username.lower():
                            continue

                        if handle in scanned_handles:
                            continue
                        scanned_handles.add(handle)
                        new_cells_in_page += 1

                        if handle not in old_handles:
                            continue

                        indicator = await cell.query_selector('div[data-testid="userFollowIndicator"]')
                        cell_text = (await cell.inner_text() or "").lower()
                        is_following_back = (
                            indicator is not None or
                            "follows you" in cell_text or
                            "关注了你" in cell_text or
                            "フォローされています" in cell_text
                        )

                        if is_following_back:
                            continue

                        # 🛡️ 接入自动化安全守卫准入校验
                        if not await self._allow_action("unfollow", f"handle:{handle}"):
                            self._print(f"  └─ ⏸️ [安全守卫] 取关操作已达限额或处于熔断保护状态，跳过 @{handle}")
                            continue

                        unfollow_btn = await cell.query_selector('button[data-testid$="-unfollow"]')
                        if not unfollow_btn:
                            btns = await cell.query_selector_all('button')
                            for b in btns:
                                label = (await b.get_attribute("aria-label") or "") + (await b.inner_text() or "")
                                if any(w in label for w in ["Following", "正在关注", "フォロー中"]):
                                    unfollow_btn = b
                                    break

                        if unfollow_btn and await is_element_fully_loaded(unfollow_btn):
                            confirmation_opened = False
                            try:
                                if await safe_human_click(page, unfollow_btn, self.personality):
                                    confirmation_opened = True
                                    await asyncio.sleep(random.uniform(0.8, 1.5))
                                    confirm_btn = None
                                    try:
                                        confirm_btn = await page.wait_for_selector(
                                            'button[data-testid="confirmationSheetConfirm"]',
                                            timeout=3000
                                        )
                                    except Exception:
                                        confirm_btn = await page.query_selector('button[data-testid="confirmationSheetConfirm"]')
                                    if confirm_btn and await is_element_fully_loaded(confirm_btn):
                                        if await safe_human_click(page, confirm_btn, self.personality):
                                            confirmation_opened = False
                                            unfollowed_count += 1
                                            self._record_action("unfollow", f"handle:{handle}")
                                            if getattr(self, "history_pool", None):
                                                self.history_pool.remove_handle(account_key, handle)
                                            self._print(f"  └─ 🧹 取关未回关博主 @{handle}（超过 {config.unfollow_threshold_days} 天未回关），成功释放 1 个关注配额")
                                            await asyncio.sleep(random.uniform(2.5, 4.0))
                            finally:
                                if confirmation_opened:
                                    # 异常闭环释放：若确认弹窗超时未消失，按取消或 Escape 安全释放遮罩
                                    try:
                                        cancel_btn = await page.query_selector('button[data-testid="confirmationSheetCancel"]')
                                        if cancel_btn and hasattr(cancel_btn, "click"):
                                            await cancel_btn.click()
                                        else:
                                            keyboard = getattr(page, "keyboard", None)
                                            if keyboard:
                                                await keyboard.press("Escape")
                                    except Exception:
                                        pass
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception as cell_err:
                        self.logger.debug("取关巡检单个 UserCell 异常: %s", cell_err)
                        continue

                # 若已达到目标或者本屏没有新博主出现，提前停止下滚
                if unfollowed_count >= max_unfollow or new_cells_in_page == 0:
                    break

                # 否则向下微滚加载下一批关注者
                if scroll_page < max_scroll_pages - 1:
                    await self._scroll_with_control(page, distance=random.randint(400, 600))
                    await asyncio.sleep(random.uniform(1.5, 2.5))

            self._cleaned_unreciprocated_today = True
            if unfollowed_count > 0:
                self._print(f"🧹 [比例维护] 本次巡检完成，共优雅释放 {unfollowed_count} 位未回关名额，账号权重更健康！")
            else:
                self._print("🧹 [比例维护] 本次巡检完成，当前视口博主均已回关或已释放。")
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self.logger.debug("未回关清理非阻断异常: %s", e)

        return unfollowed_count

    async def navigate_to_keyword_search(self, page: Any, keyword: str) -> None:

        clean_keyword = keyword.strip() if keyword else ""
        if not clean_keyword:
            self._print("🏠 [拓客目标: X 首页推荐流] 未指定检索关键词，进入首页 (为你推荐) 展开全真拟人拓客...")
            target_search_url = "https://x.com/home"
        else:
            self._print(f"🔍 准备搜索关键词: '{clean_keyword}'")
            kw_encoded = quote(clean_keyword, safe='')
            target_search_url = f"https://x.com/search?q={kw_encoded}&f=live"

        try:
            await page.goto(target_search_url, wait_until="load", timeout=25000)

            self._print("⏳ 正在等待 X 平台网页 DOM 渲染与画面绘制...")
            ready = False
            for selector in ['input[data-testid="SearchBox_Search_Input"]', 'article', 'div[data-testid="primaryColumn"]']:
                try:
                    await page.wait_for_selector(selector, state="visible", timeout=8000)
                    ready = True
                    break
                except Exception:
                    continue

            if not ready:
                self._print("⚠️ 网页渲染较慢，追加 3 秒强制拟人缓冲...")
                await asyncio.sleep(3.0)

            reaction_time = random.uniform(2.5, 4.5)
            self._print(f"✅ 网页完全加载渲染完毕，真人视觉反应延迟: {reaction_time:.2f} 秒")
            await asyncio.sleep(reaction_time)

            if clean_keyword and "/explore" in page.url.lower():
                self._print("⚠️ 页面被强制重定向至 /explore，这通常代表当前浏览器未登录账号！")
                is_logged = await self._check_login_status(page)
                if not is_logged:
                    raise NotLoggedInError(self.tag)
            elif not clean_keyword:
                self._print("✅ 首页加载成功，已切入【为你推荐】推文流！")
            else:
                self._print("✅ 检索页面跳转成功，已切入最新推文流！")
                # 🛡️ 立即检测搜索主信息流是否出现【出错了/重试】卡片并即刻自愈
                await self._check_and_handle_retry(page, current_keyword=clean_keyword)
            self._last_search_refresh_time = time.monotonic()

        except NotLoggedInError:
            raise
        except Exception as e:
            self._print(f"⚠️ 页面跳转/渲染等待过程捕获到异常: {e}")

    async def refresh_latest_search_feed(self, page: Any, keyword: str) -> bool:
        """定时重新检索/刷新最新推文流：返回到搜索页面或首页顶部，拉取刚刚发布的鲜活内容"""
        clean_keyword = keyword.strip() if keyword else ""
        if not clean_keyword:
            self._print("🔄 [推荐流刷新] 正在重载 X 首页【为你推荐】推文流...")
            try:
                # 1. 尝试检测并点击 X 首页常驻的“新推文提示浮动胶囊”
                new_posts_pill = await page.query_selector(
                    'div[data-testid="pill-new-posts"], [data-testid="pill-new-posts"], '
                    'button[aria-label*="new posts" i], div[role="button"][aria-label*="新推文" i]'
                )
                if new_posts_pill and await new_posts_pill.is_visible():
                    try:
                        await new_posts_pill.click()
                        await asyncio.sleep(random.uniform(1.5, 2.5))
                        self._last_search_refresh_time = time.monotonic()
                        self._print("✨ [推荐流刷新] 已点击首页【最新推文】浮动提示，已置顶最新内容！")
                        return True
                    except Exception:
                        pass

                try:
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

                await page.goto("https://x.com/home", wait_until="load", timeout=25000)
                for selector in ['input[data-testid="SearchBox_Search_Input"]', 'article', 'div[data-testid="primaryColumn"]']:
                    try:
                        await page.wait_for_selector(selector, state="visible", timeout=6000)
                        break
                    except Exception:
                        continue

                await asyncio.sleep(random.uniform(1.8, 3.0))
                self._last_search_refresh_time = time.monotonic()
                self._print("✅ [推荐流刷新] 首页推荐流已成功刷新置顶！")
                return True
            except Exception as exc:
                self._print(f"⚠️ [推荐流刷新] 刷新遇到轻微异常，回退完整首页导航: {exc}")
                await self.navigate_to_keyword_search(page, "")
                self._last_search_refresh_time = time.monotonic()
                return False

        self._print(f"🔄 [最新流刷新] 正在重载 '{clean_keyword}' 最新推文，置顶抓取最新鲜发布的内容...")
        try:
            # 1. 尝试检测并点击 X 网页端常驻的“新推文提示浮动胶囊”（如果有）
            new_posts_pill = await page.query_selector(
                'div[data-testid="pill-new-posts"], [data-testid="pill-new-posts"], '
                'button[aria-label*="new posts" i], div[role="button"][aria-label*="新推文" i]'
            )
            if new_posts_pill and await new_posts_pill.is_visible():
                try:
                    await new_posts_pill.click()
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    self._last_search_refresh_time = time.monotonic()
                    self._print("✨ [最新流刷新] 已点击网页【最新推文】浮动提示，已瞬间切入最新鲜发布的内容流！")
                    return True
                except Exception:
                    pass

            # 2. 若未出现浮动胶囊或已向下滚动较深，通过平滑滚顶并直接重新导航检索 Live 链接置顶刷新
            kw_encoded = quote(clean_keyword, safe='')
            target_search_url = f"https://x.com/search?q={kw_encoded}&f=live"

            try:
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.4)
            except Exception:
                pass

            await page.goto(target_search_url, wait_until="load", timeout=25000)

            for selector in ['input[data-testid="SearchBox_Search_Input"]', 'article', 'div[data-testid="primaryColumn"]']:
                try:
                    await page.wait_for_selector(selector, state="visible", timeout=6000)
                    break
                except Exception:
                    continue

            await asyncio.sleep(random.uniform(1.8, 3.0))
            await self._check_and_handle_retry(page, current_keyword=clean_keyword)
            self._last_search_refresh_time = time.monotonic()
            self._print("✅ [最新流刷新] 搜索流已成功刷新至最新（Live）时间线顶部！")
            return True
        except Exception as exc:
            self._print(f"⚠️ [最新流刷新] 快速刷新遇到轻微异常，回退完整检索导航: {exc}")
            await self.navigate_to_keyword_search(page, keyword)
            self._last_search_refresh_time = time.monotonic()
            return False

    # ==================== 扩展拟人交互：书签、转推、ChatGPT AI 回复 ====================

    async def _add_bookmark(self, page: Any, article: Any, target_key: str = "") -> bool:
        """为目标推文加入【书签（Bookmark）】"""
        key = target_key or await self._article_target_key(article)
        if not await self._allow_action("bookmark", key):
            return False
        try:
            bookmark_btn = await article.query_selector('button[data-testid="bookmark"]')
            if bookmark_btn and await is_element_fully_loaded(bookmark_btn):
                label = ""
                if hasattr(bookmark_btn, "get_attribute"):
                    label = (await bookmark_btn.get_attribute("aria-label") or "").lower()
                if "remove" in label or "削除" in label or "移除" in label:
                    return False
                if await safe_human_click(page, bookmark_btn, self.personality):
                    self._record_action("bookmark", key)
                    self._print("  └─ 🔖 [高权重社交] 成功将目标推文加入书签（Bookmark）！")
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    return True
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception:
            pass
        return False

    async def _do_retweet(self, page: Any, article: Any, target_key: str = "") -> bool:
        """偶发【转推（Retweet）】目标推文"""
        key = target_key or await self._article_target_key(article)
        if not await self._allow_action("retweet", key):
            return False
        try:
            retweet_btn = await article.query_selector('button[data-testid="retweet"]')
            if retweet_btn and await is_element_fully_loaded(retweet_btn):
                label = ""
                if hasattr(retweet_btn, "get_attribute"):
                    label = (await retweet_btn.get_attribute("aria-label") or "").lower()
                if "undo" in label or "取り消す" in label or "撤销" in label:
                    return False
                if await safe_human_click(page, retweet_btn, self.personality):
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    confirm_retweet = await page.query_selector('div[data-testid="retweetConfirm"]')
                    if confirm_retweet and await safe_human_click(page, confirm_retweet, self.personality):
                        self._record_action("retweet", key)
                        self._print("  └─ 🔁 [偶发转推] 成功转推（Retweet）了该推文！")
                        await asyncio.sleep(random.uniform(2.0, 4.0))
                        return True
                    else:
                        # 菜单弹出但确认按钮未点击成功，派发 Escape 关闭菜单防遮挡
                        if hasattr(page, "keyboard") and hasattr(page.keyboard, "press"):
                            await page.keyboard.press("Escape")
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception:
            try:
                if hasattr(page, "keyboard") and hasattr(page.keyboard, "press"):
                    await page.keyboard.press("Escape")
            except Exception:
                pass
        return False

    async def _do_ai_comment_reply(self, page: Any, article: Any, target_key: str = "") -> bool:
        """调用 ChatGPT 模型生成回复（熔断保护 + 异步线程解耦 + 模态框安全闭环释放）"""
        if self._ai_circuit_broken:
            self._print("  └─ ⚡ [熔断保护] AI 接口处于冷却保护状态，跳过本条评论生成")
            return False

        key = target_key or await self._article_target_key(article)
        if not await self._allow_action("reply", key):
            return False

        modal_opened = False
        try:
            tweet_text = await article.inner_text()
            reply_text = await asyncio.to_thread(generate_ai_reply, tweet_text)

            # 成功重置连续失败计数
            self._consecutive_ai_failures = 0

            reply_btn = await article.query_selector('button[data-testid="reply"]')
            if not reply_btn:
                return False

            # 🛡️ 预检 Who can reply 权限限制 (避免盲目点击受限推文触发气泡并误入弹窗清理)
            try:
                is_disabled = (
                    await reply_btn.get_attribute("disabled") is not None
                    or await reply_btn.get_attribute("aria-disabled") == "true"
                )
                if is_disabled:
                    self._print("  └─ 🔒 [推文受限] 该推文已被作者限制回复权限（非关注者/未被提及不可评论），智能跳过")
                    return False
            except Exception:
                pass

            if await safe_human_click(page, reply_btn, self.personality):
                # 确认模态弹窗是否真正唤起 (防止无响应时误标 modal_opened)
                modal = None
                try:
                    modal = await page.wait_for_selector(
                        'div[role="dialog"][aria-modal="true"], div[data-testid="sheetDialog"]',
                        timeout=3500
                    )
                except Exception:
                    pass

                if not modal:
                    modal = await page.query_selector('div[role="dialog"][aria-modal="true"], div[data-testid="sheetDialog"]')

                if not modal:
                    self._print("  └─ ℹ️ 回复模态框未唤起（可能推文受限或页面加载延迟），安全跳过")
                    return False

                modal_opened = True
                await asyncio.sleep(random.uniform(1.8, 2.8))

                # 🛡️ 输入框精准限定在 Dialog 模态框内部，严防误敲入首页顶部发推输入框
                input_box = await modal.query_selector('div[data-testid="tweetTextarea_0"]')
                if not input_box:
                    input_box = await page.query_selector('div[role="dialog"] div[data-testid="tweetTextarea_0"]')

                if input_box and await is_element_fully_loaded(input_box):
                    await human_type_text(page, input_box, reply_text)
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                    send_btn = await modal.query_selector('button[data-testid="tweetButton"]')
                    if not send_btn:
                        send_btn = await page.query_selector('div[role="dialog"] button[data-testid="tweetButton"]')

                    if send_btn and await safe_human_click(page, send_btn, self.personality):
                        self._record_action("reply", key)
                        self._print("  └─ 💬 [ChatGPT 拟人回复] 评论发送成功！")
                        modal_opened = False
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                        return True
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self._consecutive_ai_failures += 1
            self._print(f"  └─ ⚠️ AI 评论回复未完成 ({self._consecutive_ai_failures}/3): {e}")

            if self._consecutive_ai_failures >= 3:
                self._ai_circuit_broken = True
                self._print("  └─ 🚨 [熔断触发] AI 接口连续 3 次异常，已暂停本批次评论，防止重复兜底！")
        finally:
            if modal_opened:
                # 弹窗未成功发送，执行闭环释放，防止全屏遮罩导致后续点击失效
                try:
                    # 1. 尝试点击关闭按钮
                    close_btn = await page.query_selector(
                        'div[role="dialog"] button[data-testid="app-bar-close"], '
                        'div[role="dialog"] button[aria-label="Close"], '
                        'div[role="dialog"] button[aria-label="关闭"]'
                    )
                    if close_btn and hasattr(close_btn, "click"):
                        await close_btn.click()
                    else:
                        keyboard = getattr(page, "keyboard", None)
                        if keyboard:
                            await keyboard.press("Escape")
                    await asyncio.sleep(0.8)

                    # 2. 如果弹出“放弃草稿？”确认抽屉 (Discard post?)，显式等待并点击确认放弃
                    discard_btn = None
                    try:
                        discard_btn = await page.wait_for_selector(
                            'button[data-testid="confirmationSheetConfirm"]',
                            timeout=2500
                        )
                    except Exception:
                        discard_btn = await page.query_selector('button[data-testid="confirmationSheetConfirm"]')

                    if discard_btn and hasattr(discard_btn, "click"):
                        await discard_btn.click()
                        await asyncio.sleep(0.5)
                except Exception as clean_err:
                    self.logger.debug("清理未发送评论弹窗异常: %s", clean_err)
        return False


    async def _like_and_engage_post(self, page: Any, article: Any, config: AutomationConfig) -> int:
        """多维度拟人社交行为组合执行器（带热度感知动态评论过滤）"""
        likes_added = 0
        target_key = await self._article_target_key(article)
        first_like_btn = await article.query_selector('button[data-testid="like"]')
        if first_like_btn and await self._allow_action("like", target_key) and await safe_human_click(page, first_like_btn, self.personality):
            self._record_action("like", target_key)
            self._print("  └─ 👍 成功点赞了目标推文")
            likes_added += 1
            await asyncio.sleep(random.uniform(1.5, 3.0))

        if random.random() < config.bookmark_ratio:
            await self._add_bookmark(page, article, target_key)

        if random.random() < config.retweet_ratio:
            await self._do_retweet(page, article, target_key)

        # 🛡️ 极致细节：热度感知评论——解析互动量，避免在 0 赞 0 转推的死寂推文下留言
        if config.ai_reply_ratio > 0.0 and random.random() < config.ai_reply_ratio:
            try:
                article_inner = await article.inner_text()
                has_engagement = bool(re.search(r'[\d\.]+\s*[KMkm万]?', article_inner))
                if has_engagement or random.random() < 0.30:  # 70% 要求有互动，30% 允许冷门推文
                    if await self._do_ai_comment_reply(page, article, target_key):
                        self._comments_total += 1
                else:
                    self._print("  └─ ⏩ [热度感知] 推文属于零互动冷门帖子，跳过评论仅点赞/关注")
            except CONTROL_EXCEPTION_TYPES:
                raise
            except Exception:
                pass

        return likes_added

    # =========================================================================

    async def _browse_home_feed(self, page: Any, config: Any = None) -> int:
        likes_added = 0
        self._print("🎲 [拟人消痕] 随机切回 For You 首页逛街刷帖...")
        try:
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=12000)
            await asyncio.sleep(random.uniform(3.0, 5.0))

            for _ in range(random.randint(2, 4)):
                await self._scroll_with_control(page, distance=random.randint(400, 800))
                await asyncio.sleep(random.uniform(2.5, 5.0))

                if random.random() < 0.20:
                    articles = await page.query_selector_all("article")
                    if articles:
                        art = random.choice(articles[:3])
                        if not await is_element_fully_loaded(art):
                            continue

                        # 🛡️ 严格审查推文内容：杜绝盲赞色情/黄推/广告内容被 X 连坐限流
                        art_text = ""
                        if hasattr(art, "inner_text"):
                            try:
                                art_text = await art.inner_text()
                            except Exception:
                                art_text = ""
                        is_bad, _ = AccountFilterGuard.is_spam_or_nsfw(art_text)
                        if not is_bad and getattr(config, "blacklist_filter_enabled", True) and getattr(config, "blacklist_words", None):
                            is_bl_art, _ = check_user_blacklist(
                                {"display_name": "", "screen_name": "", "bio": "", "recent_tweets": [art_text]},
                                config.blacklist_words,
                                {"check_name": False, "check_bio": False, "check_tweets": True},
                            )
                            if is_bl_art:
                                is_bad = True
                        if is_bad:
                            continue

                        target_key = await self._article_target_key(art)
                        if not await self._allow_action("like", target_key):
                            continue

                        like_btn = await art.query_selector('button[data-testid="like"]')
                        if like_btn and await is_element_fully_loaded(like_btn):
                            btn_label = ""
                            if hasattr(like_btn, "get_attribute"):
                                btn_label = await like_btn.get_attribute("aria-label") or ""
                            if "Liked" not in btn_label and "已赞" not in btn_label and "いいね済" not in btn_label:
                                if await safe_human_click(page, like_btn, self.personality):
                                    self._record_action("like", target_key)
                                    self._print("  └─ 随机点赞了推荐页一条推文（模拟真人闲逛）")
                                    likes_added += 1
                                    await asyncio.sleep(random.uniform(2.0, 4.0))

        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self._print(f"⚠️ 逛推荐页时产生非致命异常: {e}")
        return likes_added

    async def _get_followers_robust(self, page: Any, handle: str, container: Any = None) -> tuple[int, int]:
        handle_lower = handle.lower()

        for _ in range(5):
            if handle_lower in self.user_cache:
                info = self.user_cache[handle_lower]
                return info.get("followers_count", -1), info.get("statuses_count", -1)
            await asyncio.sleep(0.3)

        if not container and hasattr(page, "query_selector"):
            try:
                container = await page.query_selector(
                    'div[data-testid="HoverCard"], div[id="layers"] div[data-testid="HoverCard"], div[id="layers"] div[role="dialog"]'
                )
            except Exception:
                pass

        if container:
            try:
                follower_links = await container.query_selector_all('a[href*="followers"]')
                for link in follower_links:
                    text = await link.inner_text()
                    parsed = self._parse_followers_from_text(text)
                    if parsed >= 0:
                        self._print(f"  └─ 💡 [HoverCard 节点解析] @{handle} 粉丝数: {parsed}")
                        return parsed, -1

                card_text = await container.inner_text()
                parsed = self._parse_followers_from_text(card_text)
                if parsed >= 0:
                    self._print(f"  └─ 💡 [HoverCard 全文解析] @{handle} 粉丝数: {parsed}")
                    return parsed, -1
            except Exception:
                pass

        return -1, -1

    async def _interact_on_profile_page(self, page: Any, handle: str, keyword: str) -> tuple[bool, int]:
        self._print(f"  └─ 🚶 [深度拟人] 决定点击主页链接，进入博主 @{handle} 的主页深读...")
        followed = False
        likes = 0

        try:
            unavailable = await page.query_selector('svg[data-testid="icon-lock"], [data-testid="empty_state"]')
            if not unavailable:
                unavailable = await page.query_selector(r'text=/\bprotected\b|受保护/i')
            if unavailable:
                self._print(f"Profile @{handle}: restricted or empty content; skipping interaction.")
                if getattr(self, "history_pool", None):
                    self.history_pool.mark_visited(self.tag, handle.lower())
                await self.navigate_to_keyword_search(page, keyword)
                return followed, likes

            await self._scroll_with_control(page, distance=400)
            await asyncio.sleep(random.uniform(3.0, 5.0))

            # 严格限定在 UserProfileHeader_Root 容器内定位关注按钮，杜绝右侧栏推荐博主误点
            profile_header = await page.query_selector('div[data-testid="UserProfileHeader_Root"]')
            follow_btn = None
            if profile_header and await is_element_fully_loaded(profile_header):
                follow_btn = await profile_header.query_selector('button[data-testid$="-follow"]')
            if not follow_btn:
                self._print(f"Profile @{handle}: no action button inside a validated header; skipping follow.")
            profile_target = f"handle:{handle.lower()}"
            if follow_btn and await self._allow_action("follow", profile_target) and await safe_human_click(page, follow_btn, self.personality):
                follow_ok, follow_reason = await self._verify_follow_success(page)
                if follow_ok:
                    self._record_action("follow", profile_target)
                    self._print(f"  └─ ➕ [主页关注] 在个人主页成功关注博主 -> @{handle}")
                    followed = True
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                else:
                    self._print(f"  └─ ⚠️ [主页关注] 关注未被平台接受: {follow_reason}")
                    if "RATE_LIMITED" in follow_reason:
                        await self._trigger_follow_limit_circuit_break(page, follow_reason)

            articles = await page.query_selector_all("article")
            for art in articles[:3]:
                like_btn = await art.query_selector('button[data-testid="like"]')
                if like_btn and await is_element_fully_loaded(like_btn):
                    btn_label = await like_btn.get_attribute("aria-label") or ""
                    if "Liked" not in btn_label and "已赞" not in btn_label and "いいね済" not in btn_label:
                        target_key = await self._article_target_key(art, handle)
                        if await self._allow_action("like", target_key) and await safe_human_click(page, like_btn, self.personality):
                            self._record_action("like", target_key)
                            likes += 1
                            self._print(f"  └─ 👍 [主页点赞] 在 @{handle} 主页点赞第 {likes} 条推文")
                            await asyncio.sleep(random.uniform(3.0, 5.0))

            self._print(f"  └─ 🔙 拜访完毕，尝试返回搜索推文列表...")
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=8000)
                await asyncio.sleep(random.uniform(2.5, 4.0))
            except Exception:
                self._print("  └─ ⚠️ 返回超时，重新导航拉回搜索轨道...")
                await self.navigate_to_keyword_search(page, keyword)

        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self._print(f"⚠️ 主页深度拜访产生异常，执行安全拉回: {e}")
            await self.navigate_to_keyword_search(page, keyword)

        return followed, likes

    async def _interact_on_profile_page_simple(self, page: Any, handle: str) -> tuple[bool, int]:
        newly_followed = False
        already_following = False
        likes = 0
        try:
            for profile_ready_sel in ['div[data-testid="primaryColumn"]', 'div[data-testid="UserProfileHeader_Root"]', 'article']:
                try:
                    await page.wait_for_selector(profile_ready_sel, timeout=5000)
                    break
                except Exception:
                    continue

            # 优先秒级预检是否为私密/锁推账号或冻结/受限异常账号（防止无效下滚与无效长停留）
            try:
                lock_icon = await page.query_selector('div[data-testid="UserProfileHeader_Root"] svg[data-testid="icon-lock"], svg[data-testid="icon-lock"]')
                if lock_icon:
                    self._print(f"  └─ 🔒 [私密账号] 博主 @{handle} 主页受保护（锁推），安全跳过")
                    return False, 0
                empty_state = await page.query_selector('div[data-testid="emptyState"], div[data-testid="error-detail"]')
                state_text = (await empty_state.inner_text() or "").strip() if empty_state and hasattr(empty_state, "inner_text") else ""
                if not state_text:
                    primary_col = await page.query_selector('div[data-testid="primaryColumn"]')
                    if primary_col and hasattr(primary_col, "inner_text"):
                        p_txt = (await primary_col.inner_text() or "")
                        state_text = p_txt[:500]
                if state_text and any(w in state_text.lower() for w in ["凍結", "suspended", "アカウントは停止", "存在しません", "doesn’t exist", "doesn't exist"]):
                    self._print(f"  └─ ⚠️ 博主 @{handle} 账号异常（被冻结/受限/不存在），安全跳过")
                    return False, 0
            except Exception:
                pass

            await asyncio.sleep(random.uniform(1.0, 1.8))
            await self._scroll_with_control(page, distance=250)
            await asyncio.sleep(random.uniform(1.2, 2.2))

            # 身份特征安全拦截校验（蓝勾/金勾、戏仿、Bot）
            if getattr(self, "_active_config", None):
                try:
                    profile_header = await page.query_selector('div[data-testid="UserProfileHeader_Root"]')
                    header_text = await profile_header.inner_text() if profile_header else ""
                    has_badge = False
                    if profile_header:
                        has_badge = (await profile_header.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
                        follower_link = await profile_header.query_selector('a[href*="/verified_followers"], a[href*="/followers"]')
                        if follower_link:
                            f_text = await follower_link.inner_text()
                            f_count = self._parse_followers_from_text(f_text)
                            if f_count > self._active_config.max_follower_threshold and f_count != -1:
                                self._print(f"  └─ ⏩ 跳过博主 @{handle}: 主页核验粉丝数 {f_count} 超过门槛 ({self._active_config.max_follower_threshold})")
                                if getattr(self, "history_pool", None):
                                    self.history_pool.mark_visited(self.tag, handle.lower())
                                return False, 0
                    cached_u = self.user_cache.get(handle.lower(), {})
                    if cached_u.get("verified"):
                        has_badge = True
                    is_p_bad, p_bad_reason = AccountFilterGuard.check_candidate_account(
                        name=cached_u.get("name", "") or header_text,
                        handle=handle,
                        card_or_dom_text=f"{header_text} {cached_u.get('description', '')}",
                        has_verified_badge=has_badge,
                        config=self._active_config,
                    )
                    if is_p_bad:
                        self._print(f"  └─ ⏩ 跳过博主 @{handle}: {p_bad_reason}")
                        return False, 0
                except Exception as filter_err:
                    self.logger.debug("主页身份特征研判异常: %s", filter_err)

            # 提取主页个人简介与最新推文文本
            recent_tweets: list[str] = []
            bio_text = ""
            try:
                tweet_elements = await page.query_selector_all("article div[lang]")
                for tw_el in tweet_elements[:4]:
                    t_txt = await tw_el.inner_text()
                    if t_txt:
                        recent_tweets.append(t_txt)
                bio_el = await page.query_selector('div[data-testid="UserDescription"]')
                bio_text = await bio_el.inner_text() if bio_el else ""
            except Exception:
                pass

            # 拟人“视线驻留”：根据博主个人资料长短动态模拟真实阅读停留
            bio_dwell = min(3.5, max(1.0, 0.8 + len(bio_text) * 0.012 + random.gauss(0.4, 0.2)))
            self._print(f"  └─ 👁️ 视线驻留阅读博主资料 ({bio_dwell:.1f} 秒)...")
            await self._sleep_with_control(bio_dwell)

            # --- 阶段 A1: 动态全字段黑名单词库一票否决校验 (Name, Handle, Bio, Recent Tweets) ---
            if self._active_config and getattr(self._active_config, "blacklist_filter_enabled", True) and getattr(self._active_config, "blacklist_words", None):
                profile_user_data = {
                    "display_name": header_text.split("\n")[0] if header_text else "",
                    "screen_name": handle,
                    "bio": bio_text or header_text,
                    "recent_tweets": recent_tweets,
                }
                is_bl, hit_word = check_user_blacklist(
                    profile_user_data,
                    self._active_config.blacklist_words,
                    self._active_config.check_options,
                )
                if is_bl:
                    self._print(f"  └─ 🚫 [黑名单一票否决] 博主 @{handle} 命中敏感词 '{hit_word}'，严禁操作，安全跳过")
                    if getattr(self, "history_pool", None):
                        self.history_pool.mark_visited(self.tag, handle.lower())
                    return False, 0

            # --- 阶段 A2: 真人度自学习与特征研判 (可配置自定义开启) ---
            if getattr(self._active_config, "authenticity_learning_enabled", False):
                try:
                    from agent.authenticity_learner import AuthenticityLearner
                    is_auth, a_score, a_reason, extracted_feats = AuthenticityLearner.evaluate_profile_authenticity(
                        handle=handle,
                        bio=bio_text,
                        tweets=recent_tweets,
                        follower_count=f_count if "f_count" in locals() and f_count != -1 else 100,
                        following_count=100
                    )
                    if not is_auth:
                        self._print(f"  └─ ⏩ [真人度学习拦截] 博主 @{handle} 未通过生活质感筛选: {a_reason}")
                        return False, 0
                    else:
                        self._print(f"  └─ 🧠 [真人度认证通过] @{handle} 评分 {a_score}分: {a_reason}")
                        self._current_profile_features = extracted_feats
                except Exception as learn_err:
                    self.logger.debug("真人度研判异常: %s", learn_err)

            unfollow_selectors = [
                'button[data-testid$="-unfollow"]',
                'button[aria-label*="Following"]',
                'button[aria-label*="フォロー中"]',
                'button[aria-label*="已关注"]',
                'button[aria-label*="Pending"]',
                'button[aria-label*="Requested"]',
                'button[aria-label*="リクエスト中"]',
                'button[aria-label*="已申请"]',
                'button[data-testid*="pending"]',
                'button[data-testid*="-cancel"]',
            ]
            # 资料卡容器限定：优先在主页头部或主栏容器内检测关注状态，杜绝侧边栏推荐大V误漂移
            profile_scope = await page.query_selector('div[data-testid="UserProfileHeader_Root"], div[data-testid="primaryColumn"]')
            target_scope = profile_scope if profile_scope else page

            for u_sel in unfollow_selectors:
                if await target_scope.query_selector(u_sel):
                    self._print(f"  └─ ⏩ 博主 @{handle} 已经处于关注或申请中状态，跳过")
                    already_following = True
                    break

            if already_following:
                return False, 0

            # 1. 扫描博主主页推文，优先执行点赞（关注必点赞，确保对方必收到互动通知）
            can_try_like = True
            if self._active_config and not bool(getattr(self._active_config, "allow_like", True)):
                can_try_like = False
            elif getattr(self, "safety_guard", None):
                guard_check = self.safety_guard.allow_action("like", "")
                if not bool(getattr(guard_check, "allowed", guard_check)):
                    can_try_like = False

            articles = []
            if can_try_like:
                for _ in range(3):
                    articles = await page.query_selector_all("article")
                    if articles:
                        break
                    await self._scroll_with_control(page, distance=300)
                    await asyncio.sleep(1.0)

            # 动态行为熵：打破机械双赞，严格遵循“关注必点赞”
            entropy_roll = random.random()
            if entropy_roll < 0.65:
                target_likes = 1
                inspect_comments = False
            elif entropy_roll < 0.95:
                target_likes = 2
                inspect_comments = False
            else:
                target_likes = 1
                inspect_comments = True

            if articles:
                for idx, art in enumerate(articles[:4]):
                    if likes >= target_likes:
                        break
                    # 跳过广告/推广推文
                    if await self._is_promoted_tweet(art):
                        continue
                    try:
                        like_btn = await art.query_selector('button[data-testid="like"]')
                        if not like_btn:
                            like_btn = await art.query_selector('button[aria-label*="Like"], button[aria-label*="いいね"]')
                        if like_btn and await is_element_fully_loaded(like_btn):
                            btn_label = await like_btn.get_attribute("aria-label") or ""
                            test_id = await like_btn.get_attribute("data-testid") or ""
                            if test_id != "unlike" and not any(k in btn_label for k in ["Liked", "已赞", "いいね済", "取り消す", "Undo"]):
                                target_key = await self._article_target_key(art, handle)
                                if await self._allow_action("like", target_key) and await safe_human_click(page, like_btn, self.personality):
                                    self._record_action("like", target_key)
                                    likes += 1
                                    self._print(f"  └─ 👍 [先赞后粉 #{likes}/{target_likes}] 成功点赞了 @{handle} 的最新推文 (通知已触发)")
                                    if inspect_comments:
                                        self._print(f"  └─ 👁️ 视线驻留查看推文详情与评论 1.8 秒...")
                                        await asyncio.sleep(random.uniform(1.6, 2.4))
                                    else:
                                        dwell = random.uniform(1.2, 2.5)
                                        await asyncio.sleep(dwell)
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception as err:
                        self.logger.debug("主页推文元素点赞异常: %s", err)
                        continue
            else:
                self._print(f"  └─ ℹ️ 博主 @{handle} 暂无公开推文（潜水读者号），保持关注意向")

            # 2. 点赞完成后，执行关注与状态核验（优先限定在主页头部或主栏容器，杜绝误点右侧栏推荐博主）
            if likes > 0:
                between_dwell = random.uniform(1.2, 2.8)
                await self._sleep_with_control(between_dwell)

            # 点赞推文时页面已向下滚动，平滑回顶确保主页头部与关注按钮 100% 进入可视视口
            if articles:
                try:
                    await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
                    await asyncio.sleep(0.6)
                except Exception:
                    pass

            follow_btn = None
            for _ in range(3):
                follow_btn = await target_scope.query_selector('button[data-testid$="-follow"], button[aria-label*="Follow"], button[aria-label*="フォロー"]')
                if not follow_btn:
                    follow_btn = await page.query_selector('div[data-testid="primaryColumn"] button[data-testid$="-follow"], button[data-testid$="-follow"], button[aria-label*="Follow"], button[aria-label*="フォロー"]')
                if follow_btn and (not hasattr(follow_btn, "is_visible") or await follow_btn.is_visible()):
                    break
                await asyncio.sleep(0.4)

            profile_target = f"handle:{handle.lower()}"

            if follow_btn and await self._allow_action("follow", profile_target):
                clicked_ok = await safe_human_click(page, follow_btn, self.personality)
                if not clicked_ok and hasattr(follow_btn, "click"):
                    try:
                        await follow_btn.click(timeout=3000)
                        clicked_ok = True
                    except Exception:
                        pass

                if clicked_ok:
                    follow_ok, follow_reason = await self._verify_follow_success(page, container=target_scope)
                    if follow_ok:
                        self._record_action("follow", profile_target)
                        if likes > 0:
                            self._print(f"  └─ ➕ [点赞后关注] 状态核验已生效 -> @{handle}")
                        else:
                            self._print(f"  └─ ➕ [主页关注] 状态核验已生效 -> @{handle}")
                        newly_followed = True
                        if getattr(self, "history_pool", None):
                            self.history_pool.mark_visited(self.tag, handle.lower())
                    else:
                        self._print(f"  └─ ⚠️ [动作反查预警] 关注 @{handle} 后未确认变更为已关注: {follow_reason}")
                        if "RATE_LIMITED" in follow_reason:
                            await self._trigger_follow_limit_circuit_break(page, follow_reason)

                # 优先尊重用户配置的频控延时区间
                min_d = float(getattr(self._active_config, "action_min_delay", 0.0) or 0.0)
                max_d = float(getattr(self._active_config, "action_max_delay", 0.0) or 0.0)
                if min_d > 0 and max_d >= min_d:
                    follow_dwell = random.uniform(min_d, max_d)
                else:
                    follow_dwell = random.uniform(0.8, 1.8) if getattr(self._active_config, "execution_preset", "safe") == "turbo" else random.uniform(1.5, 3.0)
                await self._sleep_with_control(follow_dwell)
        except CONTROL_EXCEPTION_TYPES:
            raise
        except Exception as e:
            self._print(f"  └─ ⚠️ 主页互动轻微异常: {e}")

        if newly_followed:
            try:
                if hasattr(page, "evaluate"):
                    await page.evaluate("() => window.scrollTo(0, 0)")
            except Exception:
                pass

        return newly_followed, likes

    async def _ensure_correct_followers_tab(self, page: Any) -> bool:
        """
        在关系网 Tab 栏中精准定位并点击【关注者 (Followers)】Tab。
        排除【认证关注者 / 認証済みフォロワー / Verified Followers】与【你认识的关注者 / 知り合いのフォロワー】。
        多语言中日英适配支持：
        - 中文：关注者、追蹤者、關注者
        - 日文：フォロワー
        - 英文：Followers
        """
        try:
            curr_url = str(getattr(page, "url", "") or "").lower()
            if curr_url.endswith("/followers") and "verified" not in curr_url and "_you_follow" not in curr_url:
                return True

            tabs = await page.query_selector_all('div[role="tablist"] a[role="tab"], div[role="tablist"] a, a[role="tab"]')
            for tab in tabs:
                try:
                    href = (await tab.get_attribute("href") or "").lower()
                    text = (await tab.inner_text() or "").strip()
                    # 必须避开认证/你认识的关注者
                    is_verified = any(v in text or v in href for v in ["认证", "認証", "verified"])
                    is_known = any(k in text or k in href for k in ["你认识", "知り合い", "followers_you_follow", "know"])
                    if is_verified or is_known:
                        continue

                    # 匹配全部关注者特征
                    is_followers = (
                        href.endswith("/followers")
                        or any(pos in text for pos in ["关注者", "フォロワー", "Followers", "追蹤者", "關注者"])
                    )
                    if is_followers:
                        aria_selected = (await tab.get_attribute("aria-selected") or "").lower()
                        if aria_selected == "true":
                            return True
                        self._print("  └─ 🎯 智能匹配并切换至【关注者】Tab...")
                        await safe_human_click(page, tab, self.personality)
                        await asyncio.sleep(random.uniform(1.2, 2.0))
                        return True
                except Exception:
                    continue
        except Exception as tab_err:
            self.logger.debug("Tab 校验异常: %s", tab_err)
        return False

    async def _ensure_correct_following_tab(self, page: Any) -> bool:
        """在关系网 Tab 栏中确保处于【正在关注 (Following)】Tab。"""
        try:
            curr_url = str(getattr(page, "url", "") or "").lower()
            if curr_url.endswith("/following"):
                return True
            tabs = await page.query_selector_all('div[role="tablist"] a[role="tab"], div[role="tablist"] a, a[role="tab"]')
            for tab in tabs:
                try:
                    href = (await tab.get_attribute("href") or "").lower()
                    text = (await tab.inner_text() or "").strip()
                    if href.endswith("/following") or any(pos in text for pos in ["正在关注", "フォロー中", "Following", "正在追蹤", "正在關注"]):
                        aria_selected = (await tab.get_attribute("aria-selected") or "").lower()
                        if aria_selected == "true":
                            return True
                        self._print("  └─ 🎯 智能匹配并切换至【正在关注】Tab...")
                        await safe_human_click(page, tab, self.personality)
                        await asyncio.sleep(random.uniform(1.2, 2.0))
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    async def _navigate_to_profile_network_list(
        self,
        page: Any,
        start_handle: str,
        source: str = "followers",
    ) -> bool:
        """
        导航并进入博主的关系网列表（支持：followers 关注者/粉丝，或 following 正在关注）。
        多语言中日英自动适配：
        - 关注者：中 (关注者 / 追蹤者 / 關注者)、日 (フォロワー)、英 (Followers)
        - 正在关注：中 (正在关注 / 正在追蹤 / 正在關注)、日 (フォロー中)、英 (Following)
        处理 X 前端重定向：如果点击关注者后停留在 verified_followers 或 followers_you_follow，
        自动识别中日文字并切换到【关注者】Tab。
        """
        clean_handle = start_handle.strip().lstrip("@")
        source_clean = "followers" if str(source).strip().lower() != "following" else "following"
        target_subpath = "followers" if source_clean == "followers" else "following"
        source_label = "关注者 (Followers)" if source_clean == "followers" else "正在关注 (Following)"
        target_url = f"https://x.com/{clean_handle}/{target_subpath}"
        curr_url = str(getattr(page, "url", "") or "").lower()

        # 1. 若当前已经在对应列表页（例如原位休眠唤醒），直接核验 Tab 状态
        if f"/{clean_handle.lower()}/{target_subpath}" in curr_url:
            if source_clean == "followers":
                await self._ensure_correct_followers_tab(page)
            else:
                await self._ensure_correct_following_tab(page)
            return True

        self._print(f"  └─ 👆 正在从博主 @{clean_handle} 主页进入【{source_label}】列表...")

        # 2. 尝试从个人主页寻找并点击对应链接（SPA 无刷新跳转）
        nav_clicked = False
        try:
            if source_clean == "followers":
                follower_link = await page.query_selector(
                    f'a[href="/{clean_handle}/followers"], a[href$="/followers"], '
                    f'a[href="/{clean_handle}/verified_followers"], a[href$="/verified_followers"], '
                    f'a[href*="/followers"]'
                )
                if not follower_link:
                    follower_link = await page.query_selector(
                        'a[href*="followers"][role="link"], '
                        'a[aria-label*="关注者"], a[aria-label*="フォロワー"], a[aria-label*="Followers"]'
                    )
                if follower_link and await is_element_fully_loaded(follower_link):
                    nav_clicked = await safe_human_click(page, follower_link, self.personality)
                    await asyncio.sleep(random.uniform(2.0, 3.0))
            else:
                following_link = await page.query_selector(
                    f'a[href="/{clean_handle}/following"], a[href$="/following"], a[href*="/following"]'
                )
                if not following_link:
                    following_link = await page.query_selector(
                        'a[href*="following"][role="link"], '
                        'a[aria-label*="正在关注"], a[aria-label*="フォロー中"], a[aria-label*="Following"]'
                    )
                if following_link and await is_element_fully_loaded(following_link):
                    nav_clicked = await safe_human_click(page, following_link, self.personality)
                    await asyncio.sleep(random.uniform(2.0, 3.0))
        except Exception as click_err:
            self.logger.debug("点击关系网入口链接异常: %s", click_err)

        # 3. 检查当前页面是否已到达目标，若未到达或停留在其他 Tab，执行纠偏
        curr_url = str(getattr(page, "url", "") or "").lower()
        if source_clean == "followers":
            if "verified_followers" in curr_url or "followers_you_follow" in curr_url or f"/{clean_handle.lower()}/followers" not in curr_url:
                switched = await self._ensure_correct_followers_tab(page)
                curr_url = str(getattr(page, "url", "") or "").lower()
                if not switched and f"/{clean_handle.lower()}/followers" not in curr_url:
                    self._print(f"  └─ 🧭 平滑直达博主关注者页: @{clean_handle}/followers...")
                    try:
                        await self._safe_goto(page, target_url, timeout=15000)
                        await asyncio.sleep(2.0)
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception as e:
                        self.logger.debug("导航直达 followers 异常: %s", e)
                        return False
            await self._ensure_correct_followers_tab(page)
        else:
            if f"/{clean_handle.lower()}/following" not in curr_url:
                self._print(f"  └─ 🧭 平滑直达博主关注页: @{clean_handle}/following...")
                try:
                    await self._safe_goto(page, target_url, timeout=15000)
                    await asyncio.sleep(2.0)
                except CONTROL_EXCEPTION_TYPES:
                    raise
                except Exception as e:
                    self.logger.debug("导航直达 following 异常: %s", e)
                    return False
            else:
                await self._ensure_correct_following_tab(page)

        return True

    async def _scout_deep_harvest_following_exploration(
        self,
        page: Any,
        start_handle: str,
        config: AutomationConfig,
        current_chain_depth: int,
        current_total_exec: int,
        remaining_batch_budget: int,
    ) -> tuple[int, int, int, int]:
        """【透视专属】零风控·同好圈层深度裂变拓客流水线 (BFS 同层横向收割 + 先赞后粉 + 原地自然休眠策略 A)"""
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0

        await self._assert_no_challenge(page)

        harvest_cap = getattr(config, "max_harvest_per_seed", 10)
        current_harvested = self._seed_harvest_counts.get(start_handle.lower(), 0)

        # 1. 饱和上限检查：若该博主已经采摘达到饱和上限，主动回溯退出，杜绝过度挖掘触发社交爬虫风控
        if current_harvested >= harvest_cap:
            self._print(f"  └─ 🏁 [圈层饱和] 博主 @{start_handle} 累计已收割 {current_harvested} 位同好，已达上限 ({harvest_cap}人)，安全回退...")
            self._in_situ_list_active = False
            await self._safe_go_back(page)
            return exec_count, likes_count, follows_count, views_count

        if exec_count >= remaining_batch_budget or (current_total_exec + exec_count) >= config.daily_task_limit:
            return exec_count, likes_count, follows_count, views_count

        chain_hop_source = getattr(config, "chain_hop_source", "followers").strip().lower()
        source = "followers" if chain_hop_source != "following" else "following"
        source_subpath = "followers" if source == "followers" else "following"
        source_label = "关注者 (Followers)" if source == "followers" else "正在关注 (Following)"
        target_list_url = f"https://x.com/{start_handle}/{source_subpath}"
        curr_url = str(getattr(page, "url", "") or "").lower()

        # 2. 导航至目标关系网列表（关注者或正在关注，支持中日双语自动识别与 Tab 切换）
        if f"/{start_handle.lower()}/{source_subpath}" not in curr_url:
            nav_ok = await self._navigate_to_profile_network_list(page, start_handle, source=source)
            if not nav_ok:
                self._print(f"  └─ ⏩ 博主 @{start_handle} {source_label}列表未公开/私密，退出顺藤摸瓜")
                self._in_situ_list_active = False
                await self._safe_go_back(page)
                return exec_count, likes_count, follows_count, views_count

        await self._assert_no_challenge(page)

        # 3. 扫描并透视当前关注列表中的 UserCell 候选人
        user_cells = []
        for wait_round in range(4):
            user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
            if user_cells:
                break
            await self._scroll_with_control(page, distance=250)
            await asyncio.sleep(1.5)

        views_count += len(user_cells)

        if not user_cells:
            self._print(f"  └─ ℹ️ 博主 @{start_handle} 关注列表为空或未公开，安全回退...")
            self._in_situ_list_active = False
            await self._safe_go_back(page)
            return exec_count, likes_count, follows_count, views_count

        # 4. 提取并根据透视数据/DOM 质检筛选出符合准入门槛的同好候选人
        candidate_friends: list[tuple[str, Any]] = []

        async def _collect_qualified_cells(cells: list[Any]) -> None:
            nonlocal candidate_friends
            for cell in cells:
                if len(candidate_friends) >= (remaining_batch_budget - exec_count) + 5:
                    break
                try:
                    if hasattr(cell, "evaluate"):
                        conn_res = cell.evaluate("el => el.isConnected")
                        if hasattr(conn_res, "__await__"):
                            conn_res = await conn_res
                        if conn_res is False:
                            continue
                except Exception:
                    pass

                try:
                    user_link = await cell.query_selector('a[href^="/"][role="link"]')
                    if not user_link:
                        continue
                    href = await user_link.get_attribute("href") or ""
                    handle = href.strip("/").split("/")[0].split("?")[0]
                    if not handle or handle.lower() == getattr(self, "my_username", "").lower() or handle.lower() in self._session_processed_handles:
                        continue
                    if any(c[0].lower() == handle.lower() for c in candidate_friends):
                        continue

                    handle_lower = handle.lower()

                    # ⚡ [透视前置排雷 1]：若底层透视拦截已被判定淘汰（如粉丝超标、私密锁推、机器人广告Spam等），0毫秒直过
                    if handle_lower in self._scout_rejected_handles:
                        rejection_reason = self._scout_rejection_reasons.get(handle_lower, "未达到底层透视准入门槛")
                        self.logger.debug("[透视前置排雷] 同好 @%s 底层透视已淘汰: %s", handle, rejection_reason)
                        continue

                    # ⚡ [透视前置排雷 2]：内存 user_cache 锁推核验 (0毫秒排雷)
                    cached_info = self.user_cache.get(handle_lower, {})
                    if cached_info.get("protected", False):
                        self._print(f"  └─ 🔒 [透视前置排雷] 同好 @{handle} 底层识别为私密锁推号，0耗时跳过...")
                        continue

                    # 3. DOM 预检锁推图标（兜底防御，包含多种常见 SVG 锁推选择器）
                    is_locked = False
                    try:
                        if hasattr(cell, "query_selector"):
                            is_locked = (await cell.query_selector('svg[data-testid="icon-lock"], [data-testid="icon-lock"], svg[aria-label*="protected"], svg[aria-label*="Private"], svg[aria-label*="非公開"]')) is not None
                    except Exception:
                        pass
                    if is_locked:
                        self._print(f"  └─ 🔒 [列表前置排雷] 同好 @{handle} 包含锁推标记，免跳转跳过...")
                        continue

                    # 4. 检查是否已关注
                    unfollow_btn = await cell.query_selector('button[data-testid$="-unfollow"], button[data-testid*="pending"], button[data-testid*="-cancel"]')
                    if unfollow_btn:
                        continue

                    # 5. 检查历史库去重
                    if getattr(self, "history_pool", None) and self.history_pool.is_visited(self.tag, handle_lower):
                        continue

                    # 6. 结合底层数据流透视预筛（若已在透视候选池中，说明各项指标已完全合格，直接纳选）
                    if handle_lower in self._scout_candidates:
                        candidate_friends.append((handle, user_link))
                        continue

                    # 7. 文本特征与黑名单敏感词综合过滤
                    cell_text = await cell.inner_text() if hasattr(cell, "inner_text") else ""
                    cell_verified = False
                    if hasattr(cell, "query_selector"):
                        try:
                            cell_verified = (await cell.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
                        except Exception:
                            pass

                    is_filtered, filter_reason = AccountFilterGuard.check_candidate_account(
                        name=cell_text,
                        handle=handle,
                        card_or_dom_text=cell_text,
                        has_verified_badge=cell_verified,
                        config=config,
                    )
                    if is_filtered:
                        continue

                    # 8. 获取粉丝数与阈值核验 (优先使用内存缓存)
                    hover_card = None
                    follower_count = cached_info.get("followers_count", -1)
                    if follower_count < 0:
                        if await human_move_to_fast(page, user_link, self.personality):
                            hover_card = await self._detect_hover_card_with_human_dwell(page)
                            follower_count, _ = await self._get_followers_robust(page, handle, hover_card)
                            await self._dismiss_hover_card(page)

                    if 0 <= follower_count <= config.max_follower_threshold:
                        candidate_friends.append((handle, user_link))
                except CONTROL_EXCEPTION_TYPES:
                    raise
                except Exception:
                    continue

        await _collect_qualified_cells(user_cells)

        # 若首屏合格人选较少，平滑微滚加载下一屏
        if len(candidate_friends) < remaining_batch_budget:
            await self._scroll_with_control(page, distance=400)
            await asyncio.sleep(random.uniform(1.2, 1.8))
            more_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
            views_count += len(more_cells)
            await _collect_qualified_cells(more_cells)

        # 5. 全场景自适应决策模型判定
        n_candidates = len(candidate_friends)
        if n_candidates == 0:
            self._print(f"  └─ ℹ️ [自适应决策·N=0] 博主 @{start_handle} 列表中暂无合规素人同好，安全回退推文流...")
            self._in_situ_list_active = False
            await self._safe_go_back(page)
            return exec_count, likes_count, follows_count, views_count
        elif n_candidates <= 2:
            self._print(f"  └─ 🎯 [自适应决策·N={n_candidates}] 发现 {n_candidates} 位合规素人同好，执行精准建联并在完成后回退...")
        elif 3 <= n_candidates <= 5:
            self._print(f"  └─ 💎 [自适应决策·N={n_candidates}] 适中优质圈层 ({n_candidates}人)，刚好契合单批次预算，逐个先赞后粉...")
        else:
            self._print(f"  └─ 🚀 [自适应决策·富矿N={n_candidates}] 发现超级同好圈层 ({n_candidates}人)！启动分批切片收割与原地休眠策略...")

        # 6. 单人闭环流水线（进主页 ➜ 先赞后粉 ➜ 原路后退 ➜ 列表拟人停留）
        for cand_idx, (cand_handle, cand_link) in enumerate(candidate_friends):
            if exec_count >= remaining_batch_budget or (current_total_exec + exec_count) >= config.daily_task_limit:
                break

            if (self._seed_harvest_counts.get(start_handle.lower(), 0)) >= harvest_cap:
                self._print(f"  └─ 🏁 [圈层饱和] 博主 @{start_handle} 累计已收割 {harvest_cap} 人，已达饱和上限，停止继续采摘...")
                break

            await self._assert_no_challenge(page)
            self._session_processed_handles.add(cand_handle.lower())

            # 云端协同去重
            allowed, claim_reason = await self._cloud_claim_target(cand_handle, config)
            if not allowed:
                self._print(f"  └─ ⏩ [云端去重] 同好 @{cand_handle} 已被认领: {claim_reason}")
                continue

            self._print(f"  └─ 🎯 [顺藤建联 ({cand_idx + 1}/{n_candidates})] 尝试前往同好 @{cand_handle} 主页...")
            click_ok = False
            try:
                active_link = await page.query_selector(f'a[href="/{cand_handle}"][role="link"], a[href^="/{cand_handle}?"][role="link"], a[href="/{cand_handle}"]')
                if not active_link and cand_link:
                    active_link = cand_link
                if active_link and await is_element_fully_loaded(active_link):
                    click_ok = await safe_human_click(page, active_link, self.personality)
            except Exception:
                click_ok = False

            if not click_ok:
                try:
                    await self._safe_goto(page, f"https://x.com/{cand_handle}", referer=target_list_url, timeout=15000)
                    click_ok = True
                except Exception:
                    pass

            if not click_ok:
                self._print(f"  └─ ⚠️ 无法进入同好 @{cand_handle} 主页，排查下一位...")
                continue

            await asyncio.sleep(random.uniform(2.5, 3.8))
            newly_followed, l_cnt = await self._interact_on_profile_page_simple(page, cand_handle)
            likes_count += l_cnt

            if newly_followed:
                follows_count += 1
                exec_count += 1
                self._seed_harvest_counts[start_handle.lower()] = self._seed_harvest_counts.get(start_handle.lower(), 0) + 1
                await self._cloud_confirm_target(cand_handle, config)
                if getattr(self, "history_pool", None):
                    self.history_pool.mark_visited(self.tag, cand_handle.lower())

                self._report_progress(
                    processed_count=current_total_exec + exec_count,
                    likes=self._comments_total,
                    follows=current_total_exec + exec_count,
                )

            # 7. 原生后退返回博主关注列表 (history.back)
            if newly_followed:
                self._print(f"  └─ 🔙 [原路后退] 完成 @{cand_handle} 建联，无损后退返回 @{start_handle} 列表...")
            else:
                self._print(f"  └─ 🔙 [原路后退] 跳过 @{cand_handle}，无损后退返回 @{start_handle} 列表...")
            back_ok = await self._safe_go_back(page)
            if not back_ok or f"/{start_handle.lower()}/{source_subpath}" not in str(getattr(page, "url", "")).lower():
                await self._navigate_to_profile_network_list(page, start_handle, source=source)
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # 8. 列表页拟人停顿浏览（根据是否产生写操作进行差异化微歇）
            if exec_count < remaining_batch_budget and (self._seed_harvest_counts.get(start_handle.lower(), 0)) < harvest_cap:
                if newly_followed:
                    dwell_time = random.uniform(18.0, 28.0)
                    self._print(f"  └─ ⏱️ [同好列表拟人微歇] 完成建联，原位列表自然驻留 {dwell_time:.1f} 秒，打散高频特征...")
                    await self._sleep_with_natural_dwell(page, dwell_time)
                else:
                    # 产生 0 互动（如锁推/异常跳过），模拟真人视线回移扫视 2.5~4.5 秒，快速排查下一位
                    skip_dwell = random.uniform(2.5, 4.5)
                    self._print(f"  └─ ⏱️ [同好列表扫视微歇] 目标跳过(0写操作)，列表轻度扫视停留 {skip_dwell:.1f} 秒，排查下一位...")
                    await asyncio.sleep(skip_dwell)

        # 9. 批次完工判定：是否触发策略 A 原地休眠
        total_seed_done = self._seed_harvest_counts.get(start_handle.lower(), 0)
        has_reached_budget = (exec_count >= remaining_batch_budget)
        can_in_situ_rest = (
            has_reached_budget
            and total_seed_done < harvest_cap
            and getattr(config, "in_situ_rest_enabled", True)
            and (current_total_exec + exec_count) < config.daily_task_limit
        )

        if can_in_situ_rest:
            self._in_situ_list_active = True
            self._in_situ_seed_handle = start_handle
            self._print(
                f"🏠 [原位自然休眠触发] 博主 @{start_handle} 列表已收割 {total_seed_done}/{harvest_cap} 人，"
                f"本批次配额达成，锁定当前列表视口，准备执行原地自然休眠！"
            )
            # 🌟 绝不调用 _safe_go_back，原地保留页面！
        else:
            self._in_situ_list_active = False
            if total_seed_done >= harvest_cap:
                self._print(f"🏁 [圈层收割饱和] 博主 @{start_handle} 累计已收割 {total_seed_done} 人达饱和上限 ({harvest_cap})，安全回退推文流...")
            else:
                self._print(f"🔙 [顺藤收工回溯] 博主 @{start_handle} 关注列表扫描完毕，安全回退推文流...")
            await self._safe_go_back(page)

        return exec_count, likes_count, follows_count, views_count

    async def _chain_hop_following_exploration(
        self,
        page: Any,
        start_handle: str,
        config: AutomationConfig,
        current_chain_depth: int,
        current_total_exec: int,
        remaining_batch_budget: int,
    ) -> tuple[int, int, int, int]:
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0

        await self._assert_no_challenge(page)

        # 🛡️ 透视模式专属门禁：
        # 只有在【同时开启了深挖 (chain_hop_enabled)】并且【开启了底层透视 (graphql_scout_filter_enabled)】时，
        # 才启动【零风控·同好圈层深度裂变拓客流水线】（全场景自适应透视 + 原地休眠策略 A + 先赞后粉 + 饱和上限）。
        # 若未开启透视，则 100% 保持原有无透视经典轻量逻辑！
        is_scout_mode = getattr(config, "graphql_scout_filter_enabled", True)
        is_chain_hop = getattr(config, "chain_hop_enabled", True)

        if not is_chain_hop:
            return 0, 0, 0, 0

        if is_scout_mode:
            return await self._scout_deep_harvest_following_exploration(
                page=page,
                start_handle=start_handle,
                config=config,
                current_chain_depth=current_chain_depth,
                current_total_exec=current_total_exec,
                remaining_batch_budget=remaining_batch_budget,
            )

        if (
            (config.max_chain_depth > 0 and current_chain_depth > config.max_chain_depth)
            or exec_count >= remaining_batch_budget
            or (current_total_exec + exec_count) >= config.daily_task_limit
        ):
            return exec_count, likes_count, follows_count, views_count


        entry_profile_url = str(getattr(page, "url", "") or "").strip()

        async def _ensure_return_to_entry() -> None:
            if not entry_profile_url:
                return
            try:
                curr_clean = str(getattr(page, "url", "") or "").split("?")[0].rstrip("/").lower()
                entry_clean = entry_profile_url.split("?")[0].rstrip("/").lower()
                if curr_clean != entry_clean and not curr_clean.endswith(f"/{start_handle.lower()}"):
                    self._print(f"  └─ 🧭 [历史栈校准] 回退位置偏差({page.url})，校准重置至: @{start_handle} 主页")
                    await self._safe_goto(page, entry_profile_url, timeout=15000)
                    await asyncio.sleep(random.uniform(1.2, 2.0))
            except Exception as calib_err:
                self.logger.debug("历史栈校准重置异常: %s", calib_err)

        chain_hop_source = getattr(config, "chain_hop_source", "followers").strip().lower()
        source = "followers" if chain_hop_source != "following" else "following"
        source_subpath = "followers" if source == "followers" else "following"
        source_label = "关注者 (Followers)" if source == "followers" else "正在关注 (Following)"
        target_list_url = f"https://x.com/{start_handle}/{source_subpath}"

        nav_ok = await self._navigate_to_profile_network_list(page, start_handle, source=source)
        if not nav_ok:
            self._print(f"  └─ ⏩ 博主 @{start_handle} {source_label}列表未公开/私密，退出顺藤摸瓜")
            await _ensure_return_to_entry()
            return exec_count, likes_count, follows_count, views_count

        await self._assert_no_challenge(page)

        user_cells = []
        for wait_round in range(3):
            user_cells = await page.query_selector_all('div[data-testid="UserCell"]')
            if not user_cells:
                user_cells = await page.query_selector_all('[data-testid="UserCell"]')
            if user_cells:
                break
            await self._scroll_with_control(page, distance=200)
            await asyncio.sleep(1.8)

        views_count += len(user_cells)

        if not user_cells:
            self._print(f"  └─ ℹ️ 博主 @{start_handle} 关注列表为空（未关注任何人）或未公开，快速回溯")
            await self._safe_go_back(page)
            await _ensure_return_to_entry()
            return exec_count, likes_count, follows_count, views_count

        self._print(f"  └─ 👥 成功进入 @{start_handle} 关注列表，首屏探测到 {len(user_cells)} 位好友，开始排查...")

        candidate_friends: list[str] = []

        for cell in user_cells[:10]:
            if exec_count >= remaining_batch_budget or (current_total_exec + exec_count) >= config.daily_task_limit:
                break

            try:
                user_link = await cell.query_selector('a[href^="/"][role="link"]')
                if not user_link:
                    continue

                href = await user_link.get_attribute("href") or ""
                handle = href.strip("/").split("/")[0].split("?")[0]

                if not handle or handle.lower() == getattr(self, "my_username", "").lower() or handle.lower() in self._session_processed_handles:
                    continue

                unfollow_btn = await cell.query_selector('button[data-testid$="-unfollow"], button[data-testid*="pending"], button[data-testid*="-cancel"]')
                if unfollow_btn:
                    continue

                # 🛡️ 预检锁推图标：跳过私密账号，避免作为无效跳板
                is_cell_protected = False
                if hasattr(cell, "query_selector"):
                    try:
                        is_cell_protected = (await cell.query_selector('svg[data-testid="icon-lock"]')) is not None
                    except Exception:
                        pass
                if is_cell_protected:
                    self._print(f"  └─ ⏩ 跳过私密好友 @{handle}: 账号已锁推，关注列表未公开")
                    continue


                cell_text = await cell.inner_text() if hasattr(cell, "inner_text") else ""
                cell_verified = False
                if hasattr(cell, "query_selector"):
                    try:
                        cell_verified = (await cell.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
                    except Exception:
                        pass

                is_cell_filtered, cell_reason = AccountFilterGuard.check_candidate_account(
                    name=cell_text,
                    handle=handle,
                    card_or_dom_text=cell_text,
                    has_verified_badge=cell_verified,
                    config=config,
                )
                if is_cell_filtered:
                    self._print(f"  └─ ⏩ 跳过好友 @{handle}: {cell_reason}")
                    await asyncio.sleep(random.uniform(0.6, 1.2))
                    continue

                self._session_processed_handles.add(handle.lower())
                if not await human_move_to_fast(page, user_link, self.personality):
                    continue

                self._print(f"  └─ 🔍 悬停检查好友名片 -> @{handle}...")
                hover_card = await self._detect_hover_card_with_human_dwell(page)
                if hover_card:
                    hover_text = await hover_card.inner_text() if hasattr(hover_card, "inner_text") else ""
                    hover_verified = cell_verified
                    if hasattr(hover_card, "query_selector"):
                        try:
                            hover_verified = hover_verified or ((await hover_card.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None)
                        except Exception:
                            pass
                    user_info = self.user_cache.get(handle.lower(), {})
                    if user_info.get("verified"):
                        hover_verified = True
                    is_h_filtered, h_reason = AccountFilterGuard.check_candidate_account(
                        name=user_info.get("name", ""),
                        handle=handle,
                        card_or_dom_text=f"{hover_text} {user_info.get('description', '')}",
                        has_verified_badge=hover_verified,
                        config=config,
                    )
                    if is_h_filtered:
                        self._print(f"  └─ ⏩ 跳过好友 @{handle}: {h_reason}")
                        await self._dismiss_hover_card(page)
                        continue

                follower_count, _ = await self._get_followers_robust(page, handle, hover_card)
                is_match = (0 <= follower_count <= config.max_follower_threshold)

                if is_match:
                    if hover_card:
                        card_text = (await hover_card.inner_text()).lower()
                        has_handle_link = await hover_card.query_selector(f'a[href*="/{handle.lower()}"]')
                        if f"@{handle.lower()}" not in card_text and not has_handle_link:
                            await self._dismiss_hover_card(page)
                            continue

                    self._print(f"  └─ 🎯 [顺藤发现同好] 好友 @{handle} (粉丝: {follower_count} <= {config.max_follower_threshold}) -> 锁定为候选同好")
                    candidate_friends.append(handle)
                    await self._dismiss_hover_card(page)
                    if len(candidate_friends) >= 2:
                        break
                else:
                    if follower_count > config.max_follower_threshold:
                        self._print(f"  └─ ⏩ 跳过好友 @{handle}: 粉丝数 {follower_count} 超过配置上限 ({config.max_follower_threshold})")
                        if getattr(self, "history_pool", None):
                            self.history_pool.mark_visited(self.tag, handle.lower())
                    await self._dismiss_hover_card(page)
            except CONTROL_EXCEPTION_TYPES:
                raise
            except Exception as cell_err:
                self.logger.debug("处理好友 UserCell 节点异常: %s", cell_err)
                continue

        if not candidate_friends and len(user_cells) <= 5:
            self._print(f"  └─ ℹ️ 博主 @{start_handle} 关注人数较少 ({len(user_cells)}人) 且均不符合门槛，快速回溯")
            await self._safe_go_back(page)
            await _ensure_return_to_entry()
            return exec_count, likes_count, follows_count, views_count

        if not candidate_friends and len(user_cells) > 5 and exec_count < remaining_batch_budget:
            self._print("  └─ 📜 顶部多为大V/官方号，向下微滚探测更多普通同好...")
            await self._scroll_with_control(page, distance=350)
            await asyncio.sleep(random.uniform(1.2, 2.0))
            user_cells = await page.query_selector_all('div[data-testid="UserCell"]')
            views_count += len(user_cells)
            for cell in user_cells[6:16]:
                if exec_count >= remaining_batch_budget or (current_total_exec + exec_count) >= config.daily_task_limit:
                    break

                try:
                    user_link = await cell.query_selector('a[href^="/"][role="link"]')
                    if not user_link:
                        continue

                    href = await user_link.get_attribute("href") or ""
                    handle = href.strip("/").split("/")[0].split("?")[0]

                    if not handle or handle.lower() == getattr(self, "my_username", "").lower() or handle.lower() in self._session_processed_handles:
                        continue

                    unfollow_btn = await cell.query_selector('button[data-testid$="-unfollow"], button[data-testid*="pending"], button[data-testid*="-cancel"]')
                    if unfollow_btn:
                        continue

                    # 🛡️ 预检锁推图标：跳过私密账号，避免作为无效跳板
                    is_cell_protected = False
                    if hasattr(cell, "query_selector"):
                        try:
                            is_cell_protected = (await cell.query_selector('svg[data-testid="icon-lock"]')) is not None
                        except Exception:
                            pass
                    if is_cell_protected:
                        self._print(f"  └─ ⏩ 跳过私密好友 @{handle}: 账号已锁推，关注列表未公开")
                        continue


                    cell_text = await cell.inner_text() if hasattr(cell, "inner_text") else ""
                    cell_verified = False
                    if hasattr(cell, "query_selector"):
                        try:
                            cell_verified = (await cell.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
                        except Exception:
                            pass

                    is_cell_filtered, cell_reason = AccountFilterGuard.check_candidate_account(
                        name=cell_text,
                        handle=handle,
                        card_or_dom_text=cell_text,
                        has_verified_badge=cell_verified,
                        config=config,
                    )
                    if is_cell_filtered:
                        self._print(f"  └─ ⏩ 跳过好友 @{handle}: {cell_reason}")
                        await asyncio.sleep(random.uniform(0.6, 1.2))
                        continue

                    self._session_processed_handles.add(handle.lower())
                    if not await human_move_to_fast(page, user_link, self.personality):
                        continue

                    self._print(f"  └─ 🔍 悬停检查好友名片 -> @{handle}...")
                    hover_card = await self._detect_hover_card_with_human_dwell(page)
                    if hover_card:
                        hover_text = await hover_card.inner_text() if hasattr(hover_card, "inner_text") else ""
                        hover_verified = cell_verified
                        if hasattr(hover_card, "query_selector"):
                            try:
                                hover_verified = hover_verified or ((await hover_card.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None)
                            except Exception:
                                pass
                        user_info = self.user_cache.get(handle.lower(), {})
                        if user_info.get("verified"):
                            hover_verified = True
                        is_h_filtered, h_reason = AccountFilterGuard.check_candidate_account(
                            name=user_info.get("name", ""),
                            handle=handle,
                            card_or_dom_text=f"{hover_text} {user_info.get('description', '')}",
                            has_verified_badge=hover_verified,
                            config=config,
                        )
                        if is_h_filtered:
                            self._print(f"  └─ ⏩ 跳过好友 @{handle}: {h_reason}")
                            await self._dismiss_hover_card(page)
                            continue

                    follower_count, _ = await self._get_followers_robust(page, handle, hover_card)
                    is_match = (0 <= follower_count <= config.max_follower_threshold)

                    if is_match:
                        if hover_card:
                            card_text = (await hover_card.inner_text()).lower()
                            has_handle_link = await hover_card.query_selector(f'a[href*="/{handle.lower()}"]')
                            if f"@{handle.lower()}" not in card_text and not has_handle_link:
                                await self._dismiss_hover_card(page)
                                continue

                        self._print(f"  └─ 🎯 [顺藤发现同好] 好友 @{handle} (粉丝: {follower_count} <= {config.max_follower_threshold}) -> 锁定为候选同好")
                        candidate_friends.append(handle)
                        await self._dismiss_hover_card(page)
                        if len(candidate_friends) >= 2:
                            break
                    else:
                        if follower_count > config.max_follower_threshold:
                            self._print(f"  └─ ⏩ 跳过好友 @{handle}: 粉丝数 {follower_count} 超过配置上限 ({config.max_follower_threshold})")
                            if getattr(self, "history_pool", None):
                                self.history_pool.mark_visited(self.tag, handle.lower())
                        await self._dismiss_hover_card(page)
                except CONTROL_EXCEPTION_TYPES:
                    raise
                except Exception as cell_err:
                    self.logger.debug("第二轮探测 UserCell 节点异常: %s", cell_err)
                    continue

        if not candidate_friends:
            self._print("  └─ ℹ️ 本轮好友列表中均未发现契合素人同好，结束顺藤摸瓜并安全回溯")
        else:
            for cand_idx, cand_handle in enumerate(candidate_friends):
                await self._assert_no_challenge(page)
                self._print(f"  └─ 🎯 [顺藤建联] 尝试前往同好 ({cand_idx + 1}/{len(candidate_friends)}) @{cand_handle}...")
                navigated_ok = False
                active_link = None
                # 1. 优先在当前实时 DOM 中动态匹配最新活动链接并拟人点击，保持自然浏览行为链
                try:
                    active_link = await page.query_selector(
                        f'a[href="/{cand_handle}"][role="link"], a[href^="/{cand_handle}?"][role="link"], a[href="/{cand_handle}"]'
                    )
                    if not active_link:
                        cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                        for cell in cells:
                            c_link = await cell.query_selector('a[href^="/"][role="link"]')
                            if c_link:
                                href = await c_link.get_attribute("href") or ""
                                if href.strip("/").split("/")[0].split("?")[0].lower() == cand_handle.lower():
                                    active_link = c_link
                                    break

                    if active_link and await is_element_fully_loaded(active_link):
                        if await safe_human_click(page, active_link, self.personality):
                            navigated_ok = True
                except CONTROL_EXCEPTION_TYPES:
                    raise
                except Exception as click_err:
                    self.logger.debug("点击候选同好 @%s 链接异常: %s", cand_handle, click_err)
                    navigated_ok = False

                # 2. 🛡️ 若因列表重绘、虚拟 DOM 滚出视口导致链接未在当前页面渲染 (active_link is None)，平滑直达兜底
                if not navigated_ok and active_link is None:
                    self._print(f"  └─ 🧭 同好 @{cand_handle} DOM 节点已重绘/不在可视区，采用安全平滑直达: x.com/{cand_handle}")
                    try:
                        cand_profile_url = f"https://x.com/{cand_handle}"
                        curr_url = getattr(page, "url", "") or target_following_url
                        navigated_ok = await self._safe_goto(page, cand_profile_url, referer=curr_url, timeout=15000)
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception as nav_err:
                        self.logger.warning("直达候选同好 @%s 主页异常: %s", cand_handle, nav_err)
                        navigated_ok = False

                if not navigated_ok:
                    self._print(f"  └─ ⚠️ 无法访问候选同好 @{cand_handle} 主页，尝试下一位候选同好...")
                    continue


                await asyncio.sleep(random.uniform(2.8, 4.2))
                newly_followed, l_cnt = await self._interact_on_profile_page_simple(page, cand_handle)
                if newly_followed:
                    follows_count += 1
                    exec_count += 1
                    await self._cloud_confirm_target(cand_handle, config)
                    if getattr(self, "history_pool", None):
                        self.history_pool.mark_visited(self.tag, cand_handle.lower())
                likes_count += l_cnt

                can_hop_deeper = (config.max_chain_depth == 0) or (current_chain_depth < config.max_chain_depth)
                if can_hop_deeper and exec_count < remaining_batch_budget:

                    cand_friends = self.user_cache.get(cand_handle.lower(), {}).get("friends_count", -1)
                    if 0 <= cand_friends < 3:
                        self._print(f"  └─ ℹ️ [顺藤防死胡同] 好友 @{cand_handle} 关注数过少 ({cand_friends} < 3)，跳过该分支深挖...")
                    else:
                        rec_e, rec_l, rec_f, rec_v = await self._chain_hop_following_exploration(
                            page,
                            cand_handle,
                            config,
                            current_chain_depth + 1,
                            current_total_exec + exec_count,
                            remaining_batch_budget - exec_count,
                        )
                        exec_count += rec_e
                        likes_count += rec_l
                        follows_count += rec_f
                        views_count += rec_v

                await self._safe_go_back(page)
                # 仅在建联成功或动作配额用尽时退出，若第 1 位跳过/未关注成功，顺畅尝试第 2 位候选同好
                if newly_followed or exec_count >= remaining_batch_budget:
                    break

        await self._safe_go_back(page)
        await _ensure_return_to_entry()
        return exec_count, likes_count, follows_count, views_count

    async def execute_task(self, page: Any, keyword: str = "", *, dry_run: bool = False, **kwargs: Any) -> dict[str, Any]:
        custom_config = {"keyword": keyword, "dry_run": dry_run}
        custom_config.update(kwargs)
        return await self.run(custom_config=custom_config)

    async def _execute_follow_burst_protection(self, consecutive_follows: int, config: AutomationConfig) -> int:
        if not getattr(config, "follow_burst_cooling_enabled", True):
            return consecutive_follows

        # ⚡ 极速模式：连续达到 5 人时进行 8~12 秒弹性微歇，既保障极速高吞吐，又平滑规避 Twitter 针对高频突发并发的 429 接口封堵
        if config.execution_preset == "turbo":
            if consecutive_follows >= 5:
                turbo_nap = random.uniform(8.0, 12.0)
                self._print(f"⚡ [极速弹性微歇] 已连续极速关注 {consecutive_follows} 人，弹性微歇 {turbo_nap:.1f} 秒防范平台频次限制...")
                await self._sleep_with_control(turbo_nap)
                return 0
            return consecutive_follows

        # 🛡️ 安全模式：仅在单批次内连续快速关注达到 3 人时，执行 15~30 秒轻度拟人微歇（绝不长时间阻断批次）
        if consecutive_follows < 3:
            return consecutive_follows

        follow_nap_time = random.uniform(15.0, 30.0)
        self._print(f"🛑 [关注拟人节奏] 当前批次已连续关注 {consecutive_follows} 人，进行拟人微歇 ({follow_nap_time:.1f} 秒)...")

        nap_remaining = int(follow_nap_time)
        while nap_remaining > 0:
            if nap_remaining % 10 == 0 or nap_remaining <= 5:
                self._print(f"⏱️ [微歇倒计时] 距离继续拓客还剩 {nap_remaining} 秒...")
            step = min(1.0, float(nap_remaining))
            await self._sleep_with_control(step)
            nap_remaining -= 1

        self._print("✅ [关注拟人节奏] 微歇完毕，继续拓客！")
        return 0

    async def _run_single_batch(
        self,
        page: Any,
        config: AutomationConfig,
        current_total_exec: int,
        current_total_likes: int = 0,
        current_total_follows: int = 0,
        current_total_views: int = 0,
    ) -> tuple[int, int, int, int]:
        batch_limit = random.randint(8, 14) if config.execution_preset == "turbo" else random.randint(3, 5)
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0
        consecutive_follows = 0
        empty_rounds = 0
        consecutive_scrolls = 0
        has_roamed_in_batch = False

        # 每个新批次重置 AI 熔断标记，重新给 API 尝试机会
        self._ai_circuit_broken = False
        self._consecutive_ai_failures = 0

        # 会话级去重集合：跨批次保留已处理博主，杜绝重复扫描 (设置安全容量上限，防止长程多开运行内存泄漏)
        if len(self._session_processed_handles) > 10000:
            self._session_processed_handles = set(list(self._session_processed_handles)[-5000:])
        if hasattr(self, "_scout_rejected_handles") and len(self._scout_rejected_handles) > 10000:
            self._scout_rejected_handles = set(list(self._scout_rejected_handles)[-5000:])
        processed_handles = self._session_processed_handles

        max_scan_rounds = 75 if getattr(config, "graphql_scout_filter_enabled", True) else 25
        for round_idx in range(max_scan_rounds):
            await self._assert_no_challenge(page)
            await self._ensure_on_track_and_clean_tabs(page, current_keyword=config.keyword)

            if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                self._print(f"🎉 当前批次目标已处理完成 ({exec_count} 人)，即将进入挂机倒计时...")
                break

            # ⏱️ 定时重新检索/刷新最新推文流检测 (仅在用户显式开启时生效，默认保持原有滚动逻辑)
            if config.periodic_search_refresh_enabled and config.search_refresh_interval_minutes > 0:
                elapsed = time.monotonic() - getattr(self, "_last_search_refresh_time", 0)
                if elapsed >= config.search_refresh_interval_minutes * 60:
                    self._print(
                        f"⏰ [定时刷新] 已达到设定间隔 ({elapsed / 60:.1f} 分钟 >= {config.search_refresh_interval_minutes} 分钟)，"
                        f"正在刷新最新内容流，捕获最新鲜发布的内容..."
                    )
                    await self.refresh_latest_search_feed(page, config.keyword)
                    empty_rounds = 0
                    consecutive_scrolls = 0
                    await asyncio.sleep(random.uniform(2.0, 3.5))
                    continue

            # 🛋️ 自然摸鱼与行为噪声注入 (深滚保护：批次深搜途中严禁频繁跳首页重置时间线，最多偶发 1 次且需在达成 3 人后)
            should_roam = False
            if (
                getattr(config, "natural_roaming_enabled", True)
                and not has_roamed_in_batch
                and exec_count >= 3
                and random.random() < 0.08
            ):
                should_roam = True

            if should_roam:
                has_roamed_in_batch = True
                roaming_likes = await self._execute_natural_roaming(page, config)
                likes_count += roaming_likes
                self._report_progress(
                    processed_count=current_total_exec + exec_count,
                    likes=current_total_likes + likes_count,
                    follows=current_total_follows + follows_count,
                    comments=self._comments_total,
                    scanned_posts=current_total_views + views_count,
                )
                continue

            consecutive_follows = await self._execute_follow_burst_protection(consecutive_follows, config)

            articles = await page.query_selector_all("article")
            if not articles:
                if await self._check_and_handle_retry(page, current_keyword=config.keyword):
                    articles = await page.query_selector_all("article")
                    if articles:
                        empty_rounds = 0

            if not articles:
                empty_rounds += 1
                if empty_rounds in (2, 4):
                    self._print(f"⚠️ [断流巡检] 连续 {empty_rounds} 次未扫描到推文，主动复查页面错误与重试按钮...")
                    if await self._check_and_handle_retry(page, current_keyword=config.keyword):
                        articles = await page.query_selector_all("article")
                        if articles:
                            empty_rounds = 0

                if not articles:
                    if empty_rounds >= 6:
                        self._print(f"🔄 [推文断流自愈] 连续 {empty_rounds} 次未检测到推文，重新拉回关键词检索流...")
                        await self.navigate_to_keyword_search(page, config.keyword)
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                        articles = await page.query_selector_all("article")
                        empty_rounds = 0
                        if not articles:
                            continue

                    if not articles:
                        self._print(f"⚠️ [第 {empty_rounds}/6 次] 当前未扫描到推文，向下滚动刷出更多推文...")
                        consecutive_scrolls += 1
                        await self._scroll_with_control(page, distance=500)
                        await asyncio.sleep(random.uniform(1.2, 2.0))
                        if getattr(config, "search_pagination_refresh_enabled", True) and (consecutive_scrolls >= 8 or empty_rounds >= 3):
                            self._print("🔄 [最新流深滚自愈] 页面出现断流或深滚达限，自动重载最新流恢复新鲜推文...")
                            await self.refresh_latest_search_feed(page, config.keyword)
                            consecutive_scrolls = 0
                            empty_rounds = 0
                        continue
            else:
                empty_rounds = 0

            views_count += len(articles)
            self._report_progress(
                processed_count=current_total_exec + exec_count,
                likes=current_total_likes + likes_count,
                follows=current_total_follows + follows_count,
                comments=self._comments_total,
                scanned_posts=current_total_views + views_count,
            )
            target_found_in_round = False

            for article in articles:
                if not await is_element_fully_loaded(article):
                    continue

                if await self._is_promoted_tweet(article):
                    continue

                article_text = await article.inner_text()
                is_bad_post, post_reason = AccountFilterGuard.is_spam_or_nsfw(article_text)
                if not is_bad_post and getattr(config, "blacklist_filter_enabled", True) and getattr(config, "blacklist_words", None):
                    is_bl_tweet, bl_tweet_word = check_user_blacklist(
                        {"display_name": "", "screen_name": "", "bio": "", "recent_tweets": [article_text]},
                        config.blacklist_words,
                        {"check_name": False, "check_bio": False, "check_tweets": True},
                    )
                    if is_bl_tweet:
                        is_bad_post = True
                        post_reason = f"命中黑名单敏感词 (特征: {bl_tweet_word})"
                if is_bad_post:
                    self._print(f"⏩ [风控拟人防御] 过滤推文: 包含{post_reason} (模拟扫视停顿...)")
                    await asyncio.sleep(random.uniform(1.2, 2.5))
                    continue

                user_links = await article.query_selector_all('div[data-testid="User-Name"] a[href^="/"]')

                for link in user_links:
                    if not await is_element_fully_loaded(link):
                        continue

                    href = await link.get_attribute("href") or ""
                    ignored_paths = ['/status/', '/analytics', '/photo/', '/search', '/i/', '/lists/', '/hashtag/']

                    if href and not any(x in href for x in ignored_paths):
                        handle = href.strip("/").split("/")[0].split("?")[0]

                        if handle and handle.lower() not in {"home", "explore", "notifications", "messages"} and handle.lower() not in processed_handles:
                            processed_handles.add(handle.lower())
                            target_found_in_round = True
                            empty_rounds = 0

                            # 🛡️ 30 天持久化去重池检索（跨天/跨会话防骚扰，脱敏判定）
                            if getattr(self, "history_pool", None) and self.history_pool.is_visited(self.tag, handle.lower()):
                                self._print(f"⏩ [历史去重记忆] 博主 @{handle} 近30天内已处理/互动过，跳过 (模拟阅读扫视停顿...)")
                                await asyncio.sleep(random.uniform(1.0, 2.2))
                                continue

                            # ⚡ 底层数据流极速透视预筛（实验性功能）
                            if getattr(config, "graphql_scout_filter_enabled", True):
                                handle_lower = handle.lower()
                                if handle_lower in self._scout_rejected_handles:
                                    scout_reason = self._scout_rejection_reasons.get(handle_lower, "未达到底层透视准入门槛")
                                    self._print(f"⚡ [底层透视秒级过滤] 博主 @{handle} 未达标: {scout_reason} (零耗时直过，省去悬停与主页跳转)")
                                    await asyncio.sleep(random.uniform(0.5, 1.0))
                                    continue
                                elif handle_lower in self._scout_candidates:
                                    scout_cand = self._scout_candidates[handle_lower]
                                    self._print(
                                        f"🎯 [底层透视命中优质目标] @{handle} "
                                        f"(粉丝: {scout_cand.get('followers_count', 0)}, "
                                        f"关注: {scout_cand.get('friends_count', 0)}) -> 靶向命中，执行触达"
                                    )

                            # 🔍 推文卡片前置指标快速质检 (零耗时排雷，节省宝贵配额)
                            if getattr(config, "pre_click_guard_enabled", True):
                                is_discard, discard_reason = await self._pre_click_card_guard(article, handle, config=config)
                                if is_discard:
                                    self._print(f"⏩ [卡片前置排雷] 跳过劣质目标 @{handle}: {discard_reason} (零耗时拦截，节省宝贵配额)")
                                    await asyncio.sleep(random.uniform(0.8, 1.8))
                                    continue
                                elif bool(re.search(r"\d{7,}$", handle)) and getattr(config, "smart_newbie_recognition_enabled", True):
                                    self._print(f"🌱 [新人智能识别] 博主 @{handle} 虽然含多位数字后缀，但拥有个性头像且发帖真实自然，判定为【真实新手素人】，予以放行准入！")

                            self._print(f"🔍 悬停检查博主名片 -> @{handle}...")
                            move_ok = await human_move_to_fast(page, link, self.personality)
                            if not move_ok:
                                break

                            hover_card = await self._detect_hover_card_with_human_dwell(page)

                            # 🛡️ 若悬停昵称未唤起名片，自动尝试悬停博主头像（Twitter 头像唤起名片成功率极高）
                            if not hover_card:
                                try:
                                    avatar_link = await article.query_selector('div[data-testid="Tweet-User-Avatar"] a[href^="/"], div[data-testid="Tweet-User-Avatar"]')
                                    if avatar_link and await human_move_to_fast(page, avatar_link, self.personality):
                                        hover_card = await self._detect_hover_card_with_human_dwell(page)
                                except Exception:
                                    pass

                            follower_count, statuses_count = await self._get_followers_robust(page, handle, hover_card)

                            user_info = self.user_cache.get(handle.lower(), {})
                            has_badge = bool(user_info.get("verified", False))
                            if hover_card and not has_badge:
                                try:
                                    badge_el = await hover_card.query_selector('svg[data-testid="icon-verified"]')
                                    if badge_el:
                                        has_badge = True
                                except Exception:
                                    pass

                            hover_dom_text = ""
                            if hover_card and hasattr(hover_card, "inner_text"):
                                try:
                                    hover_dom_text = await hover_card.inner_text()
                                except Exception:
                                    hover_dom_text = ""

                            combined_card_text = f"{user_info.get('description', '')} {hover_dom_text}".strip()
                            candidate_name = user_info.get("name", "") or (hover_dom_text.split("\n")[0] if hover_dom_text else "")

                            if article_text:
                                try:
                                    setattr(config, "_candidate_recent_tweets", [article_text])
                                except Exception:
                                    pass

                            is_candidate_bad, p_reason = AccountFilterGuard.check_candidate_account(
                                name=candidate_name,
                                handle=handle,
                                card_or_dom_text=combined_card_text,
                                has_verified_badge=has_badge,
                                config=config,
                            )
                            if is_candidate_bad:
                                await self._simulate_natural_reading(page, article, handle, p_reason)
                                await self._dismiss_hover_card(page)
                                break

                            if statuses_count > config.max_statuses_threshold and statuses_count != -1:
                                self._print(f"⏩ [风控拟人防御] 跳过博主 @{handle} (发帖量 {statuses_count} > {config.max_statuses_threshold}，模拟阅读停顿后移开...)")
                                await asyncio.sleep(random.uniform(1.8, 3.2))
                                await self._dismiss_hover_card(page)
                                break

                            is_match = (follower_count <= config.max_follower_threshold) if follower_count >= 0 else True

                            if is_match:
                                count_desc = f"{follower_count}" if follower_count >= 0 else "主页核验中"
                                self._print(f"🎯 命中目标 @{handle} (粉丝数: {count_desc} <= {config.max_follower_threshold})")

                                # 跨设备云端去重认领 (Cross-Device Cloud Deduplication Claim)
                                allowed, claim_reason = await self._cloud_claim_target(handle, config)
                                if not allowed:
                                    self._print(f"⏩ [云端去重协同] 目标 @{handle} 已被其他设备认领: {claim_reason} (拟人阅读停顿后移开...)")
                                    await asyncio.sleep(random.uniform(1.8, 3.2))
                                    await self._dismiss_hover_card(page)
                                    break

                                await asyncio.sleep(random.uniform(1.5, 3.0))

                                # 若名片未读取到粉丝数，必须切入主页核验粉丝；若已确认在阈值内，则按配置概率切主页或走名片快捷关注
                                should_visit_profile = True if follower_count < 0 else (random.random() < config.profile_visit_ratio)
                                did_hop = False
                                need_profile_visit = False
                                profile_visit_reason = ""

                                if should_visit_profile:
                                    need_profile_visit = True
                                    profile_visit_reason = "常规主页深度拜访"
                                else:
                                    hover_card_check = await page.query_selector(
                                        'div[data-testid="HoverCard"], div[id="layers"] div[data-testid="HoverCard"], div[id="layers"] div[role="dialog"]'
                                    )

                                    # 1. 检查是否已经是关注或申请中状态 (Following / 正在关注 / フォロー中 / -unfollow)
                                    already_following = False
                                    unfollow_selectors = [
                                        'div[id="layers"] button[data-testid$="-unfollow"]',
                                        'div[id="layers"] [role="button"][data-testid$="-unfollow"]',
                                        'div[id="layers"] [data-testid$="-unfollow"]',
                                        'div[id="layers"] button[aria-label*="Following"]',
                                        'div[id="layers"] button[aria-label*="正在关注"]',
                                        'div[id="layers"] button[aria-label*="已关注"]',
                                        'div[id="layers"] button[aria-label*="フォロー中"]',
                                        'div[id="layers"] [role="button"][aria-label*="Following"]',
                                        'div[id="layers"] [role="button"][aria-label*="正在关注"]',
                                        'div[id="layers"] [role="button"][aria-label*="已关注"]',
                                        'div[id="layers"] [role="button"][aria-label*="フォロー中"]',
                                    ]
                                    for u_sel in unfollow_selectors:
                                        u_btn = await page.query_selector(u_sel)
                                        if u_btn and await u_btn.is_visible():
                                            already_following = True
                                            break

                                    if already_following:
                                        self._print(f"ℹ️ 博主 @{handle} 当前已处于关注状态，跳过重复关注")
                                        if getattr(self, "history_pool", None):
                                            self.history_pool.mark_visited(self.tag, handle.lower())
                                        await self._dismiss_hover_card(page)
                                        break

                                    # 2. 定位名片关注按钮（全层级备选，严格限定在 layers 容器内，杜绝侧边栏推荐博主误点）
                                    follow_btn = None
                                    follow_candidate_selectors = [
                                        'button[data-testid$="-follow"]',
                                        'button[data-testid*="-follow"]',
                                        'button[data-testid="userFollow"]',
                                        'button[data-testid="follow"]',
                                        '[role="button"][data-testid$="-follow"]',
                                        '[role="button"][data-testid*="-follow"]',
                                        '[data-testid$="-follow"]',
                                        'button[aria-label*="Follow"]',
                                        'button[aria-label*="关注"]',
                                        'button[aria-label*="フォロー"]',
                                        '[role="button"][aria-label*="Follow"]',
                                        '[role="button"][aria-label*="关注"]',
                                        '[role="button"][aria-label*="フォロー"]',
                                    ]

                                    if hover_card_check:
                                        for sel in follow_candidate_selectors:
                                            btn = await hover_card_check.query_selector(sel)
                                            if btn and await btn.is_visible():
                                                follow_btn = btn
                                                break

                                    if not follow_btn:
                                        for sel in [
                                            'div[id="layers"] button[data-testid$="-follow"]',
                                            'div[id="layers"] button[data-testid*="-follow"]',
                                            'div[id="layers"] [role="button"][data-testid$="-follow"]',
                                            'div[id="layers"] [data-testid$="-follow"]',
                                            'div[id="layers"] button[aria-label*="Follow"]',
                                            'div[id="layers"] button[aria-label*="关注"]',
                                            'div[id="layers"] button[aria-label*="フォロー"]',
                                        ]:
                                            btn = await page.query_selector(sel)
                                            if btn and await btn.is_visible():
                                                follow_btn = btn
                                                break

                                    # 名片快捷关注前的一票否决终审防线
                                    if hover_card_check and getattr(config, "blacklist_filter_enabled", True) and getattr(config, "blacklist_words", None):
                                        try:
                                            hc_text = await hover_card_check.inner_text() if hasattr(hover_card_check, "inner_text") else ""
                                            is_hc_bl, hc_hit = check_user_blacklist(
                                                {"display_name": "", "screen_name": handle, "bio": hc_text, "recent_tweets": [article_text] if article_text else []},
                                                config.blacklist_words,
                                                config.check_options,
                                            )
                                            if is_hc_bl:
                                                self._print(f"  └─ 🚫 [名片一票否决] 博主 @{handle} 名片命中敏感词 '{hc_hit}'，取消关注")
                                                await self._dismiss_hover_card(page)
                                                break
                                        except Exception as hc_err:
                                            self.logger.debug("名片关注前终审异常: %s", hc_err)

                                    profile_target = f"handle:{handle.lower()}"
                                    card_follow_success = False
                                    follow_reason = "UNREADY"

                                    if follow_btn and await self._allow_action("follow", profile_target):
                                        if await safe_human_click(page, follow_btn, self.personality):
                                            follow_ok, follow_reason = await self._verify_follow_success(page, container=hover_card_check)
                                            if follow_ok:
                                                card_follow_success = True
                                                self._record_action("follow", profile_target)
                                                self._print(f"➕ [名片关注] 自动关注成功 -> @{handle}")
                                                follows_count += 1
                                                exec_count += 1
                                                consecutive_follows += 1
                                                await self._cloud_confirm_target(handle, config)
                                                if getattr(self, "history_pool", None):
                                                    self.history_pool.mark_visited(self.tag, handle.lower())

                                                await self._dismiss_hover_card(page)

                                                # 触发包含点赞、书签、转推、ChatGPT 评论的多维拟人社交组合拳
                                                engaged_likes = await self._like_and_engage_post(page, article, config)
                                                likes_count += engaged_likes

                                                # 顺藤摸瓜：名片关注成功后，也有机会深入该博主好友圈拓展同好
                                                remaining_batch = batch_limit - exec_count
                                                if config.chain_hop_enabled and remaining_batch > 0 and random.random() < config.chain_hop_ratio:
                                                    self._print(f"🌟 [偶发顺藤摸瓜] 顺便浏览 @{handle} 的好友关注列表...")
                                                    entered_profile = False
                                                    if await safe_human_click(page, link, self.personality):
                                                        await asyncio.sleep(random.uniform(2.5, 4.0))
                                                        entered_profile = True
                                                    elif hasattr(page, "goto"):
                                                        await self._safe_goto(page, f"https://x.com/{handle}", timeout=15000)
                                                        await asyncio.sleep(2.0)
                                                        entered_profile = True

                                                    if entered_profile:
                                                        c_e, c_l, c_f, c_v = await self._chain_hop_following_exploration(
                                                            page,
                                                            handle,
                                                            config,
                                                            current_chain_depth=1,
                                                            current_total_exec=current_total_exec + exec_count,
                                                            remaining_batch_budget=remaining_batch,
                                                        )
                                                        exec_count += c_e
                                                        likes_count += c_l
                                                        follows_count += c_f
                                                        views_count += c_v
                                                        consecutive_follows += c_f
                                                        did_hop = True
                                                        if getattr(self, "_in_situ_list_active", False):
                                                            self._print(f"🏠 [原位休眠待命] 当前批次达成，保持在博主 @{handle} 好友列表页原地休眠，不再后退...")
                                                            break
                                                        back_ok = await self._safe_go_back(page)
                                                        if not back_ok or "search" not in getattr(page, "url", "").lower():
                                                            await self.navigate_to_keyword_search(page, config.keyword)
                                            else:
                                                if "RATE_LIMITED" in follow_reason:
                                                    self._print(f"⚠️ 关注触发平台限流 -> @{handle}: {follow_reason}")
                                                    await self._dismiss_hover_card(page)
                                                    await self._trigger_follow_limit_circuit_break(page, follow_reason)
                                                    break

                                    if not card_follow_success:
                                        # 🛡️ 达标博主零流失 · 终极保底救火通道：
                                        # 若名片关注由于网络微抖动、DOM 动画重挂载或按钮未就绪未能完成，
                                        # 绝不直接跳过抛弃高价值博主，立即平滑转入博主个人主页完成稳健关注与点赞！
                                        if not follow_btn:
                                            self._print(f"🔄 名片未捕获到关注按钮，自动平滑切入 @{handle} 个人主页执行稳健关注...")
                                        else:
                                            self._print(f"🔄 名片响应存在微延迟（{follow_reason}），触发【达标博主零流失保障】，自动切入 @{handle} 个人主页执行稳健关注...")
                                        need_profile_visit = True
                                        profile_visit_reason = "名片微延迟触发主页保底"

                                if need_profile_visit:
                                    if profile_visit_reason == "常规主页深度拜访":
                                        self._print(f"🚶 点击博主昵称，进入 @{handle} 个人主页...")
                                    else:
                                        self._print(f"🚶 启动主页保底通道，进入 @{handle} 个人主页...")
                                    await self._dismiss_hover_card(page)
                                    entered_p = False
                                    try:
                                        if link and await safe_human_click(page, link, self.personality):
                                            await asyncio.sleep(random.uniform(2.5, 4.0))
                                            entered_p = True
                                    except Exception:
                                        entered_p = False

                                    if not entered_p and hasattr(page, "goto"):
                                        await self._safe_goto(page, f"https://x.com/{handle}", timeout=15000)
                                        await asyncio.sleep(2.0)
                                        entered_p = True

                                    if entered_p:
                                        p_followed, p_likes = await self._interact_on_profile_page_simple(page, handle)
                                        if p_followed:
                                            follows_count += 1
                                            exec_count += 1
                                            consecutive_follows += 1
                                            await self._cloud_confirm_target(handle, config)
                                            if getattr(self, "history_pool", None):
                                                self.history_pool.mark_visited(self.tag, handle.lower())
                                            if getattr(config, "authenticity_learning_enabled", False):
                                                try:
                                                    from agent.authenticity_learner import AuthenticityLearner
                                                    feats = getattr(self, "_current_profile_features", [])
                                                    AuthenticityLearner.record_interacted_candidate(
                                                        profile_tag=self.tag,
                                                        target_handle=handle,
                                                        action_type="follow",
                                                        features=feats
                                                    )
                                                    self._print(f"  └─ 📥 [自学习特征入库] 已将 @{handle} 生活特征 {feats} 纳入48小时回关复核跟踪池")
                                                except Exception as rec_err:
                                                    self.logger.debug("自学习入库失败: %s", rec_err)
                                            if profile_visit_reason != "常规主页深度拜访":
                                                self._print(f"✅ 【主页保底生效】博主 @{handle} 已顺利完成关注与点赞，无一人浪费！")
                                        likes_count += p_likes

                                        remaining_batch = batch_limit - exec_count
                                        if config.chain_hop_enabled and remaining_batch > 0 and random.random() < config.chain_hop_ratio:
                                            self._print(f"🌟 [偶发顺藤摸瓜] 顺便浏览 @{handle} 的好友关注列表...")
                                            c_e, c_l, c_f, c_v = await self._chain_hop_following_exploration(
                                                page,
                                                handle,
                                                config,
                                                current_chain_depth=1,
                                                current_total_exec=current_total_exec + exec_count,
                                                remaining_batch_budget=remaining_batch,
                                            )
                                            exec_count += c_e
                                            likes_count += c_l
                                            follows_count += c_f
                                            views_count += c_v
                                            consecutive_follows += c_f
                                            did_hop = True

                                        if getattr(self, "_in_situ_list_active", False):
                                            self._print(f"🏠 [原位休眠待命] 当前批次达成，保持在博主 @{handle} 好友列表页原地休眠，不再后退...")
                                            break

                                        self._print("🔙 深度拜访结束，优先后退恢复搜索推文轨道...")
                                        back_ok = await self._safe_go_back(page)
                                        if not back_ok or "search" not in getattr(page, "url", "").lower():
                                            self._print("⚠️ 无法后退恢复推文列表，平滑重新导航搜索页...")
                                            await self.navigate_to_keyword_search(page, config.keyword)
                                    else:
                                        self._print(f"⚠️ 无法进入博主 @{handle} 个人主页，跳过")

                                self._report_progress(
                                    processed_count=current_total_exec + exec_count,
                                    likes=current_total_likes + likes_count,
                                    follows=current_total_follows + follows_count,
                                    comments=self._comments_total,
                                    scanned_posts=current_total_views + views_count,
                                )

                                if config.execution_preset == "turbo":
                                    cool_down = random.uniform(15.0, 25.0)
                                    preset_tag = "⚡ [极速黄金平衡]"
                                else:
                                    cool_mult = 1.2 if did_hop else 1.0
                                    cool_down = max(3.0, self.personality.get_cooldown() * cool_mult)
                                    preset_tag = f"⏱️ [{self.personality.p_type}]"
                                self._print(f"{preset_tag} 降频休息 {cool_down:.1f} 秒 (保活游弋防断流)...")
                                await self._sleep_with_natural_dwell(page, cool_down)
                                await self._dismiss_hover_card(page)
                                # 顺着时间线向下滑动推进，持续挖掘更早时段发布的优质推文博主
                                await self._scroll_with_control(page, distance=random.randint(450, 650))
                                await asyncio.sleep(random.uniform(1.2, 2.0))
                            else:
                                self._print(f"⏩ [风控拟人防御] 跳过博主 @{handle} (粉丝数: {follower_count} > 门槛: {config.max_follower_threshold}，模拟阅读停顿后移开...)")
                                await asyncio.sleep(random.uniform(1.8, 3.2))
                                await self._dismiss_hover_card(page)
                            break
                if target_found_in_round:
                    break

            if not target_found_in_round:
                empty_rounds += 1
                consecutive_scrolls += 1
                await self._scroll_with_control(page, distance=500)

                # 🔄 最新流深滚翻页自愈机制 (防深滚卡死/空白，持续捕获最新鲜推文)
                max_consec = 35 if getattr(config, "graphql_scout_filter_enabled", True) else 8
                max_empty = 25 if getattr(config, "graphql_scout_filter_enabled", True) else 3
                if getattr(config, "search_pagination_refresh_enabled", True) and (consecutive_scrolls >= max_consec or empty_rounds >= max_empty):
                    self._print(
                        f"🔄 [最新流深滚自愈] 连续深滚已达 {consecutive_scrolls} 屏或暂未见新鲜推文，"
                        f"自动平滑回顶重载最新流，防止接口卡死与空白..."
                    )
                    await self.refresh_latest_search_feed(page, config.keyword)
                    consecutive_scrolls = 0
                    empty_rounds = 0
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                    continue

                empty_reload_limit = 20 if getattr(config, "graphql_scout_filter_enabled", True) else 6
                if empty_rounds >= empty_reload_limit:
                    self._print(f"⚠️ 连续 {empty_rounds} 轮未发现新推文，刷新页面...")
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=12000)
                        await asyncio.sleep(4.0)
                    except Exception:
                        pass
                    empty_rounds = 0
            else:
                consecutive_scrolls = 0
                empty_rounds = 0

        return exec_count, likes_count, follows_count, views_count

    def _chain_backtrack(self) -> None:
        """智能链条回溯：当当前博主的关注列表穷尽或私密时，逐层弹出并返回上级节点"""
        if self._chain_stack:
            popped = self._chain_stack.pop()
            self.logger.info("顺藤摸瓜回溯弹出节点: @%s, 剩余栈深: %d", popped, len(self._chain_stack))
        if self._chain_stack:
            parent = self._chain_stack[-1]
            self._chain_current_following_handle = parent
            self._print(f"↩️ [智能回溯] 回溯至上一级博主 @{parent} 继续探索分支同好...")
        else:
            self._chain_current_following_handle = ""
            self._print("🌱 [智能回溯] 当前链条已全部探测穷尽，已重置为关键词搜索发现全新种子...")

    async def _run_keyword_chain_fission_batch(
        self,
        page: Any,
        config: AutomationConfig,
        current_total_exec: int,
        current_total_likes: int = 0,
        current_total_follows: int = 0,
        current_total_views: int = 0,
    ) -> tuple[int, int, int, int]:
        """关键词种子·无限顺藤摸瓜 (A ➜ B ➜ C 链式裂变批次执行器)"""
        batch_limit = random.randint(8, 14) if config.execution_preset == "turbo" else random.randint(3, 5)
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0
        consecutive_follows = 0
        empty_rounds = 0

        self._ai_circuit_broken = False
        self._consecutive_ai_failures = 0
        # 会话级去重集合：跨批次保留已处理博主，杜绝重复扫描 (设置安全容量上限，防止长程多开运行内存泄漏)
        if len(self._session_processed_handles) > 10000:
            self._session_processed_handles = set(list(self._session_processed_handles)[-5000:])
        processed_handles = self._session_processed_handles

        for cycle_idx in range(30):
            await self._assert_no_challenge(page)
            await self._ensure_on_track_and_clean_tabs(page, current_keyword=config.keyword)
            if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                break

            consecutive_follows = await self._execute_follow_burst_protection(consecutive_follows, config)

            # 阶段 1: 检查当前是否有活跃的链条节点。若无，通过关键词搜索或首页推文流捕获首个素人种子博主 A
            if not self._chain_current_following_handle:
                if config.keyword:
                    self._print(f"🌱 [种子探测] 链条栈为空，正在通过关键词 '{config.keyword}' 搜索发现首个素人种子博主...")
                else:
                    self._print("🌱 [种子探测] 链条栈为空，正在通过 X 首页推荐流发现首个素人种子博主...")
                await self.navigate_to_keyword_search(page, config.keyword)
                await asyncio.sleep(random.uniform(2.5, 4.0))

                seed_found = False
                seed_rounds = 25 if getattr(config, "graphql_scout_filter_enabled", True) else 6
                for search_round in range(seed_rounds):
                    await self._assert_no_challenge(page)
                    articles = await page.query_selector_all("article")
                    if not articles:
                        if await self._check_and_handle_retry(page, current_keyword=config.keyword):
                            articles = await page.query_selector_all("article")
                    if not articles:
                        await self._scroll_with_control(page, distance=500)
                        await asyncio.sleep(random.uniform(1.5, 2.5))
                        continue

                    views_count += len(articles)
                    for article in articles:
                        if not await is_element_fully_loaded(article):
                            continue

                        if await self._is_promoted_tweet(article):
                            continue

                        article_text = await article.inner_text()
                        is_bad_post, post_reason = AccountFilterGuard.is_spam_or_nsfw(article_text)
                        if not is_bad_post and getattr(config, "blacklist_filter_enabled", True) and getattr(config, "blacklist_words", None):
                            is_bl_tweet, _ = check_user_blacklist(
                                {"display_name": "", "screen_name": "", "bio": "", "recent_tweets": [article_text]},
                                config.blacklist_words,
                                {"check_name": False, "check_bio": False, "check_tweets": True},
                            )
                            if is_bl_tweet:
                                is_bad_post = True
                        if is_bad_post:
                            continue

                        user_links = await article.query_selector_all('div[data-testid="User-Name"] a[href^="/"]')
                        for link in user_links:
                            if not await is_element_fully_loaded(link):
                                continue

                            href = await link.get_attribute("href") or ""
                            ignored_paths = ['/status/', '/analytics', '/photo/', '/search', '/i/', '/lists/', '/hashtag/']
                            if not href or any(x in href for x in ignored_paths):
                                continue

                            handle = href.strip("/").split("/")[0].split("?")[0]
                            if not handle or handle.lower() in {"home", "explore", "notifications", "messages"} or handle.lower() == getattr(self, "my_username", "").lower():
                                continue

                            if handle.lower() in processed_handles:
                                continue

                            processed_handles.add(handle.lower())

                            if getattr(self, "history_pool", None) and self.history_pool.is_visited(self.tag, handle.lower()):
                                continue

                            self._print(f"🔍 悬停检查种子候选博主名片 -> @{handle}...")
                            move_ok = await human_move_to_fast(page, link, self.personality)
                            if not move_ok:
                                continue

                            hover_card = await self._detect_hover_card_with_human_dwell(page)
                            follower_count, statuses_count = await self._get_followers_robust(page, handle, hover_card)

                            user_info = self.user_cache.get(handle.lower(), {})
                            has_badge = bool(user_info.get("verified", False))
                            if hover_card and not has_badge:
                                try:
                                    badge_el = await hover_card.query_selector('svg[data-testid="icon-verified"]')
                                    if badge_el:
                                        has_badge = True
                                except Exception:
                                    pass

                            hover_dom_text = ""
                            if hover_card and hasattr(hover_card, "inner_text"):
                                try:
                                    hover_dom_text = await hover_card.inner_text()
                                except Exception:
                                    hover_dom_text = ""

                            combined_card_text = f"{user_info.get('description', '')} {hover_dom_text}".strip()
                            candidate_name = user_info.get("name", "") or (hover_dom_text.split("\n")[0] if hover_dom_text else "")

                            if article_text:
                                try:
                                    setattr(config, "_candidate_recent_tweets", [article_text])
                                except Exception:
                                    pass

                            is_cand_bad, p_reason = AccountFilterGuard.check_candidate_account(
                                name=candidate_name,
                                handle=handle,
                                card_or_dom_text=combined_card_text,
                                has_verified_badge=has_badge,
                                config=config,
                            )
                            if is_cand_bad:
                                self._print(f"⏩ 跳过种子候选 @{handle}: {p_reason}")
                                await self._dismiss_hover_card(page)
                                continue

                            if statuses_count > config.max_statuses_threshold and statuses_count != -1:
                                await self._dismiss_hover_card(page)
                                continue

                            is_match = (follower_count <= config.max_follower_threshold) if follower_count >= 0 else True
                            if not is_match:
                                self._print(f"⏩ 跳过种子候选 @{handle}: 粉丝数 {follower_count} > 门槛 {config.max_follower_threshold}")
                                await self._dismiss_hover_card(page)
                                continue

                            # 命中种子博主
                            allowed, claim_reason = await self._cloud_claim_target(handle, config)
                            if not allowed:
                                self._print(f"⏩ [云端去重] 目标 @{handle} 已被认领: {claim_reason}")
                                await self._dismiss_hover_card(page)
                                continue

                            self._print(f"🎯 [锁定源头种子] 博主 @{handle} (粉丝数: {follower_count} <= {config.max_follower_threshold})")
                            self._print(f"🚶 进入种子博主 @{handle} 个人主页建联...")
                            entered_p = False
                            if await safe_human_click(page, link, self.personality):
                                await asyncio.sleep(random.uniform(2.5, 4.0))
                                entered_p = True
                            elif hasattr(page, "goto"):
                                await self._safe_goto(page, f"https://x.com/{handle}", timeout=15000)
                                await asyncio.sleep(2.0)
                                entered_p = True

                            if entered_p:
                                p_followed, p_likes = await self._interact_on_profile_page_simple(page, handle)
                                if p_followed:
                                    follows_count += 1
                                    exec_count += 1
                                    consecutive_follows += 1
                                    await self._cloud_confirm_target(handle, config)
                                    if getattr(self, "history_pool", None):
                                        self.history_pool.mark_visited(self.tag, handle.lower())
                                    likes_count += p_likes

                                    self._chain_stack = [handle]
                                    self._chain_current_following_handle = handle
                                    seed_found = True
                                    self._print(f"🔗 [顺藤摸瓜链条开启] 种子节点 @{handle} 就绪 (链深: 1)，即将进入 @{handle}/following 展开链式裂变...")
                                    self._report_progress(
                                        processed_count=current_total_exec + exec_count,
                                        likes=current_total_likes + likes_count,
                                        follows=current_total_follows + follows_count,
                                        comments=self._comments_total,
                                        scanned_posts=current_total_views + views_count,
                                    )
                                    break
                                else:
                                    self._print(f"⚠️ 种子候选博主 @{handle} 建联未完成（可能已关注或账号异常），安全返回推文流重新寻找种子...")
                                    back_ok = await self._safe_go_back(page)
                                    if not back_ok or "search" not in getattr(page, "url", "").lower():
                                        await self.navigate_to_keyword_search(page, config.keyword)
                                    await asyncio.sleep(random.uniform(1.5, 2.5))
                            else:
                                self._print(f"⚠️ 无法进入博主 @{handle} 主页，继续寻找其他种子")
                        if seed_found:
                            break
                    if seed_found:
                        break
                    await self._scroll_with_control(page, distance=500)
                    await asyncio.sleep(random.uniform(1.8, 2.5))

                if not seed_found:
                    self._print("⚠️ 未能在推文流中找到符合条件的种子博主，强制刷新推文流重新检索...")
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=12000)
                        await asyncio.sleep(3.0)
                    except Exception:
                        pass
                    continue

            # 阶段 2: 顺藤摸瓜裂变（从当前节点 A 的 following 列表寻找素人 B -> C...）
            current_target_handle = self._chain_current_following_handle
            current_depth = len(self._chain_stack)
            depth_label = f"第 {current_depth} 层" if config.max_chain_depth > 0 else f"深度: {current_depth} (无尽链式)"
            self._print(f"\n🔍 [顺藤摸瓜推进] 正在排查博主 @{current_target_handle} 的好友关注列表 ({depth_label})...")

            chain_hop_source = getattr(config, "chain_hop_source", "followers").strip().lower()
            source = "followers" if chain_hop_source != "following" else "following"
            source_subpath = "followers" if source == "followers" else "following"
            source_label = "关注者 (Followers)" if source == "followers" else "正在关注 (Following)"
            target_list_url = f"https://x.com/{current_target_handle}/{source_subpath}"
            curr_url = getattr(page, "url", "").lower()

            target_entered = False
            if f"/{current_target_handle.lower()}/{source_subpath}" in curr_url:
                target_entered = True
            else:
                target_entered = await self._navigate_to_profile_network_list(page, current_target_handle, source=source)

            if not target_entered:
                self._print(f"⚠️ 无法打开 @{current_target_handle} {source_label}页，执行回溯...")
                self._chain_backtrack()
                continue

            # 确保页面渲染状态稳定，等待可能的异步重定向或换页完成
            try:
                if hasattr(page, "wait_for_load_state"):
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
            except Exception:
                pass

            await self._assert_no_challenge(page)

            # 3. 检查博主关注列表是否可公开访问（避开私密锁推号或被限账号）
            try:
                empty_state = await page.query_selector('div[data-testid="emptyState"], div[data-testid="empty_state"], div[data-testid="error-detail"]')
                state_text = (await empty_state.inner_text() or "").strip() if empty_state and hasattr(empty_state, "inner_text") else ""
                if not state_text:
                    primary_col = await page.query_selector('div[data-testid="primaryColumn"]')
                    if primary_col and hasattr(primary_col, "inner_text"):
                        p_txt = (await primary_col.inner_text() or "")
                        state_text = p_txt[:500]
                if state_text and any(w in state_text.lower() for w in ["非公開", "protected", "アカウントは停止", "doesn’t exist", "doesn't exist", "存在しません", "このアカウントのツイートは非公开です"]):
                    self._print(f"🔒 博主 @{current_target_handle} 关注列表未公开或账号受保护，回溯至上层节点...")
                    self._chain_backtrack()
                    continue
            except Exception as body_err:
                if "execution context was destroyed" in str(body_err).lower() or "navigation" in str(body_err).lower():
                    await asyncio.sleep(1.5)

            # 4. 显式智能等待联系人列表或空状态渲染完成（避免网络延迟提前误判为空）
            try:
                await page.wait_for_selector(
                    'div[data-testid="UserCell"], [data-testid="UserCell"], div[data-testid="empty_state"], div[data-testid="emptyState"]',
                    timeout=8000
                )
            except Exception:
                pass

            user_cells = []
            for wait_round in range(5):
                try:
                    user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                except Exception as qc_err:
                    if "execution context was destroyed" in str(qc_err).lower() or "navigation" in str(qc_err).lower():
                        self.logger.debug("UserCell 查询遇到瞬时换页重载，缓冲 1.8 秒重试: %s", qc_err)
                        await asyncio.sleep(1.8)
                        try:
                            user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                        except Exception:
                            user_cells = []
                    else:
                        user_cells = []
                if user_cells:
                    break
                await self._scroll_with_control(page, distance=250)
                await asyncio.sleep(1.8)

            views_count += len(user_cells)

            if not user_cells:
                if await self._check_and_handle_retry(page):
                    try:
                        user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                    except Exception:
                        user_cells = []
                    views_count += len(user_cells)

            if not user_cells:
                self._print(f"ℹ️ 博主 @{current_target_handle} 关注列表为空或未公开，回溯至上层节点...")
                self._chain_backtrack()
                continue

            hop_success = False

            for scan_pass in range(3):
                if hop_success or exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                    break

                for cell in user_cells:
                    if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                        break

                    try:
                        # 活性复验：若元素已从 DOM 树脱落，直接跳过以防抛出 Detached 异常
                        try:
                            if hasattr(cell, "evaluate"):
                                is_connected = await cell.evaluate("el => el.isConnected")
                                if not is_connected:
                                    continue
                        except Exception:
                            continue

                        user_link = await cell.query_selector('a[href^="/"][role="link"]')
                        if not user_link:
                            continue

                        href = await user_link.get_attribute("href") or ""
                        cand_handle = href.strip("/").split("/")[0].split("?")[0]

                        if not cand_handle or cand_handle.lower() == getattr(self, "my_username", "").lower():
                            continue

                        if cand_handle.lower() in processed_handles:
                            continue

                        # 前置毫秒级私密锁推识别：若 UserCell 中含有锁推小图标，直接跳过，杜绝进入死胡同
                        try:
                            if hasattr(cell, "query_selector"):
                                cell_lock = await cell.query_selector('svg[data-testid="icon-lock"]')
                                if cell_lock:
                                    self._print(f"  └─ 🔒 [前置跳过私密] 好友 @{cand_handle} 为私密锁推号，毫秒级跳过")
                                    processed_handles.add(cand_handle.lower())
                                    continue
                        except Exception:
                            pass

                        # 环路防护：若已在当前链条栈中，避免 A -> B -> A 循环
                        if cand_handle.lower() in [s.lower() for s in self._chain_stack]:
                            continue

                        # 已经处于关注/待批准状态则跳过
                        unfollow_btn = await cell.query_selector('button[data-testid$="-unfollow"], button[data-testid*="pending"], button[data-testid*="-cancel"]')
                        if unfollow_btn:
                            continue

                        cell_text = await cell.inner_text() if hasattr(cell, "inner_text") else ""
                        cell_verified = False
                        if hasattr(cell, "query_selector"):
                            try:
                                cell_verified = (await cell.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
                            except Exception:
                                pass

                        is_cell_filtered, cell_reason = AccountFilterGuard.check_candidate_account(
                            name=cell_text,
                            handle=cand_handle,
                            card_or_dom_text=cell_text,
                            has_verified_badge=cell_verified,
                            config=config,
                        )
                        if is_cell_filtered:
                            self._print(f"  └─ ⏩ 跳过好友 @{cand_handle}: {cell_reason}")
                            processed_handles.add(cand_handle.lower())
                            continue

                        if getattr(self, "history_pool", None) and self.history_pool.is_visited(self.tag, cand_handle.lower()):
                            processed_handles.add(cand_handle.lower())
                            continue

                        processed_handles.add(cand_handle.lower())

                        if not await human_move_to_fast(page, user_link, self.personality):
                            continue

                        self._print(f"  └─ 🔍 悬停检查好友名片 -> @{cand_handle}...")
                        hover_card = await self._detect_hover_card_with_human_dwell(page)
                        if hover_card:
                            hover_text = await hover_card.inner_text() if hasattr(hover_card, "inner_text") else ""
                            hover_verified = cell_verified
                            if hasattr(hover_card, "query_selector"):
                                try:
                                    hover_verified = hover_verified or ((await hover_card.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None)
                                except Exception:
                                    pass
                            user_info = self.user_cache.get(cand_handle.lower(), {})
                            if user_info.get("verified"):
                                hover_verified = True
                            is_h_filtered, h_reason = AccountFilterGuard.check_candidate_account(
                                name=user_info.get("name", ""),
                                handle=cand_handle,
                                card_or_dom_text=f"{hover_text} {user_info.get('description', '')}",
                                has_verified_badge=hover_verified,
                                config=config,
                            )
                            if is_h_filtered:
                                self._print(f"  └─ ⏩ 跳过好友 @{cand_handle}: {h_reason}")
                                await self._dismiss_hover_card(page)
                                continue

                        follower_count, _ = await self._get_followers_robust(page, cand_handle, hover_card)
                        is_match = (0 <= follower_count <= config.max_follower_threshold)

                        if is_match:
                            self._print(f"  └─ 🎯 [顺藤发现同好] 好友 @{cand_handle} (粉丝数: {follower_count} <= {config.max_follower_threshold})")
                            await self._dismiss_hover_card(page)

                            allowed, claim_reason = await self._cloud_claim_target(cand_handle, config)
                            if not allowed:
                                self._print(f"  └─ ⏩ [云端去重] 好友 @{cand_handle} 已被认领: {claim_reason}")
                                continue

                            self._print(f"  └─ 🚶 顺藤点击 @{cand_handle} 进入个人主页...")
                            click_ok = False
                            try:
                                if await is_element_fully_loaded(user_link):
                                    click_ok = await safe_human_click(page, user_link, self.personality)
                            except Exception:
                                click_ok = False

                            if not click_ok:
                                try:
                                    await self._safe_goto(page, f"https://x.com/{cand_handle}", timeout=15000)
                                    click_ok = True
                                except Exception:
                                    pass

                            if not click_ok:
                                self._print(f"  └─ ⚠️ 无法进入好友 @{cand_handle} 主页，寻找下一位")
                                continue

                            await asyncio.sleep(random.uniform(2.5, 4.0))
                            p_followed, p_likes = await self._interact_on_profile_page_simple(page, cand_handle)
                            if p_followed:
                                follows_count += 1
                                exec_count += 1
                                consecutive_follows += 1
                                await self._cloud_confirm_target(cand_handle, config)
                                if getattr(self, "history_pool", None):
                                    self.history_pool.mark_visited(self.tag, cand_handle.lower())
                                likes_count += p_likes

                                self._report_progress(
                                    processed_count=current_total_exec + exec_count,
                                    likes=current_total_likes + likes_count,
                                    follows=current_total_follows + follows_count,
                                    comments=self._comments_total,
                                    scanned_posts=current_total_views + views_count,
                                )

                                can_chain_deeper = (config.max_chain_depth == 0) or (len(self._chain_stack) < config.max_chain_depth)
                                if can_chain_deeper:
                                    # 智能裂变节点健康度检查：避开正在关注数 < 3 的自闭死胡同节点或私密锁推账号
                                    cand_user_info = self.user_cache.get(cand_handle.lower(), {})
                                    cand_friends = cand_user_info.get("friends_count", -1)
                                    cand_protected = bool(cand_user_info.get("protected", False))

                                    if cand_friends == -1 or not cand_protected:
                                        try:
                                            f_link = await page.query_selector('a[href$="/following"], a[href*="/following"]')
                                            if f_link:
                                                cand_friends = self._parse_followers_from_text(await f_link.inner_text())
                                            lock_icon = await page.query_selector('div[data-testid="UserProfileHeader_Root"] svg[data-testid="icon-lock"], svg[data-testid="icon-lock"]')
                                            if lock_icon:
                                                cand_protected = True
                                        except Exception:
                                            pass

                                    if cand_protected:
                                        self._print(
                                            f"  └─ 🔒 [顺藤跳过私密] 博主 @{cand_handle} 为私密锁推账号，关注列表未公开，"
                                            f"成功关注建联但不将其作为深挖跳板，继续在 @{current_target_handle} 列表探寻公开健康节点..."
                                        )
                                    elif 0 <= cand_friends < 3:
                                        self._print(
                                            f"  └─ ℹ️ [顺藤防死胡同] 博主 @{cand_handle} 正在关注数过少 ({cand_friends} < 3)，"
                                            f"成功关注建联但不将其作为深挖跳板，继续在 @{current_target_handle} 列表探寻健康节点..."
                                        )
                                    else:
                                        self._chain_stack.append(cand_handle)
                                        self._chain_current_following_handle = cand_handle
                                        self._print(
                                            f"🔗 [顺藤摸瓜·深度递进] 链条扩展: @{current_target_handle} ➜ @{cand_handle} "
                                            f"(当前深度: {len(self._chain_stack)})，下一步直接探索 @{cand_handle} 关注列表..."
                                        )
                                        if len(self._chain_stack) % 4 == 0:
                                            try:
                                                if hasattr(page, "evaluate"):
                                                    await page.evaluate("() => { if (window.gc) window.gc(); }")
                                            except Exception:
                                                pass
                                else:
                                    self._print(
                                        f"⛓️ [顺藤摸瓜·深度上限] 已达到配置深度 ({len(self._chain_stack)}/{config.max_chain_depth})，"
                                        f"保持在 @{current_target_handle} 列表继续排查或回溯..."
                                    )

                                if self._chain_current_following_handle != cand_handle:
                                    # 未将 cand_handle 设为新跳板，安全返回当前父节点的关注列表继续探寻
                                    self._print(f"  └─ 🔙 返回 @{current_target_handle} 的关注列表继续排查...")
                                    back_ok = await self._safe_go_back(page)
                                    if not back_ok or f"/{current_target_handle.lower()}/following" not in getattr(page, "url", "").lower():
                                        await self._safe_goto(page, target_following_url, timeout=15000)
                                    await asyncio.sleep(random.uniform(1.2, 2.0))

                                hop_success = True
                                if config.execution_preset == "turbo":
                                    cool_down = random.uniform(15.0, 25.0)
                                    preset_tag = "⚡ [极速黄金平衡]"
                                else:
                                    cool_down = max(3.0, self.personality.get_cooldown() * 1.1)
                                    preset_tag = f"⏱️ [{self.personality.p_type}]"
                                if config.enable_soft_landing and (current_total_exec + exec_count) >= int(config.daily_task_limit * 0.85):
                                    cool_down = cool_down * random.uniform(1.2, 1.35)
                                self._print(f"{preset_tag} 顺藤建联后拟人停顿 {cool_down:.1f} 秒 (保活游弋防断流)...")
                                await self._sleep_with_natural_dwell(page, cool_down)
                                break
                            else:
                                self._print(f"  └─ ⚠️ 好友 @{cand_handle} 未能完成关注（可能已关注或账号异常），安全返回关注列表...")
                                back_ok = await self._safe_go_back(page)
                                if not back_ok or f"/{current_target_handle.lower()}/following" not in getattr(page, "url", "").lower():
                                    await self._safe_goto(page, target_following_url, timeout=15000)
                                await asyncio.sleep(random.uniform(1.5, 2.5))
                                continue

                        else:
                            if follower_count > config.max_follower_threshold:
                                self._print(f"  └─ ⏩ 跳过好友 @{cand_handle}: 粉丝数 {follower_count} > 上限 {config.max_follower_threshold}")
                                if getattr(self, "history_pool", None):
                                    self.history_pool.mark_visited(self.tag, cand_handle.lower())
                            await self._dismiss_hover_card(page)
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception as cell_err:
                        self.logger.debug("处理好友节点异常: %s", cell_err)
                        continue

                if not hop_success and scan_pass < 2 and exec_count < batch_limit:
                    self._print("  └─ 📜 正在向下平滑滚动，加载更多好友同好...")
                    await self._scroll_with_control(page, distance=400)
                    try:
                        user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                    except Exception:
                        user_cells = []
                    views_count += len(user_cells)

            if not hop_success:
                self._print(f"↩️ 博主 @{current_target_handle} 的关注列表已扫描完毕（未发现新契合素人），执行智能回溯...")
                self._chain_backtrack()

        return exec_count, likes_count, follows_count, views_count

    async def _run_target_followers_batch(
        self,
        page: Any,
        config: AutomationConfig,
        current_total_exec: int,
        current_total_likes: int = 0,
        current_total_follows: int = 0,
        current_total_views: int = 0,
    ) -> tuple[int, int, int, int]:
        """扫博主粉丝模式：批量抓取目标博主的粉丝列表，严格执行全字段敏感词一票否决与频控互动"""
        batch_limit = random.randint(8, 14) if config.execution_preset == "turbo" else random.randint(3, 6)
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0
        consecutive_follows = 0

        target_creators = list(config.target_creators)
        if not target_creators and config.keyword:
            target_creators = clean_target_creators(config.keyword)

        if not target_creators:
            self._print("⚠️ [扫粉丝模式] 未检测到有效的目标博主列表，请在控制中心填写目标博主 ID！")
            return 0, 0, 0, 0

        # 初始化或校准跨批次博主索引指针与已扫描统计
        if not hasattr(self, "_target_creator_idx"):
            self._target_creator_idx = 0
        if not hasattr(self, "_creator_scanned_counts"):
            self._creator_scanned_counts = {}

        if self._target_creator_idx >= len(target_creators):
            self._target_creator_idx = 0

        self._print(f"👥 [扫博主粉丝模式] 激活！目标博主总数: {len(target_creators)} 位 (当前起跑索引: #{self._target_creator_idx + 1} -> @{target_creators[self._target_creator_idx]})")
        self._print(f"🎯 单账号最大扫描量: {config.max_followers_per_target} 人/博主 | 频控延时: {config.action_min_delay}~{config.action_max_delay} 秒")
        self._print(f"🛡️ 全局黑名单敏感词库: {len(config.blacklist_words)} 个关键词已装载 (一票否决制 · NFKC 归一化)")

        processed_handles = self._session_processed_handles

        # 遍历目标博主列表（从当前索引向后轮转遍历所有博主）
        start_idx = self._target_creator_idx
        for offset in range(len(target_creators)):
            if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                break

            curr_idx = (start_idx + offset) % len(target_creators)
            raw_target = target_creators[curr_idx]
            creator_handle = raw_target.strip().lstrip("@")
            if not creator_handle:
                continue

            creator_key = creator_handle.lower()
            already_scanned = self._creator_scanned_counts.get(creator_key, 0)
            if already_scanned >= config.max_followers_per_target:
                self._print(f"ℹ️ 目标博主 @{creator_handle} 累计已扫 {already_scanned} 人（已达配置上限 {config.max_followers_per_target}），顺延切换下一博主")
                self._target_creator_idx = (curr_idx + 1) % len(target_creators)
                continue

            self._print(f"\n──────────────────────────────────────────")
            self._print(f"🎯 [目标博主 #{curr_idx + 1}/{len(target_creators)}] 正在探寻 @{creator_handle} 的粉丝列表 (当前已处理 {already_scanned}/{config.max_followers_per_target} 人)...")
            target_followers_url = f"https://x.com/{creator_handle}/followers"

            try:
                await self._safe_goto(page, target_followers_url, timeout=15000)
                await asyncio.sleep(random.uniform(2.5, 3.5))
            except CONTROL_EXCEPTION_TYPES:
                raise
            except Exception as nav_err:
                self._print(f"⚠️ 无法打开博主 @{creator_handle} 的粉丝页 ({nav_err})，跳过该博主")
                self._target_creator_idx = (curr_idx + 1) % len(target_creators)
                continue

            # 检查博主账号状态（是否冻结/私密等）
            try:
                empty_state = await page.query_selector('div[data-testid="emptyState"], div[data-testid="empty_state"], div[data-testid="error-detail"]')
                state_text = (await empty_state.inner_text() or "").strip() if empty_state and hasattr(empty_state, "inner_text") else ""
                if not state_text:
                    primary_col = await page.query_selector('div[data-testid="primaryColumn"]')
                    if primary_col and hasattr(primary_col, "inner_text"):
                        p_txt = (await primary_col.inner_text() or "")
                        state_text = p_txt[:500]
                if state_text and any(w in state_text.lower() for w in ["凍結", "suspended", "アカウントは停止", "doesn’t exist", "doesn't exist", "存在しません", "非公開"]):
                    self._print(f"🔒 博主 @{creator_handle} 粉丝列表未公开、受保护或账号异常，跳过")
                    self._target_creator_idx = (curr_idx + 1) % len(target_creators)
                    continue
            except Exception:
                pass

            # 等待粉丝列表元素加载
            try:
                await page.wait_for_selector('div[data-testid="UserCell"], [data-testid="UserCell"], div[data-testid="empty_state"]', timeout=8000)
            except Exception:
                pass

            empty_scrolls = 0
            accumulated_scroll_px = 0

            while self._creator_scanned_counts.get(creator_key, 0) < config.max_followers_per_target:
                if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                    break

                await self._assert_no_challenge(page)
                consecutive_follows = await self._execute_follow_burst_protection(consecutive_follows, config)

                user_cells = []
                try:
                    user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                except Exception:
                    user_cells = []

                if not user_cells:
                    if await self._check_and_handle_retry(page):
                        try:
                            user_cells = await page.query_selector_all('div[data-testid="UserCell"], [data-testid="UserCell"]')
                        except Exception:
                            user_cells = []

                if not user_cells:
                    empty_scrolls += 1
                    if empty_scrolls >= 4:
                        self._print(f"ℹ️ 博主 @{creator_handle} 的粉丝列表未加载出数据或已触底，切换至下一目标博主")
                        self._target_creator_idx = (curr_idx + 1) % len(target_creators)
                        break
                    scroll_dist = random.randint(280, 480)
                    await self._scroll_with_control(page, distance=scroll_dist)
                    accumulated_scroll_px += scroll_dist
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    continue

                views_count += len(user_cells)

                # 第一阶段：在当前粉丝列表页面提取符合 Phase 1 初筛的候选人名单（绝不在此期间跳离页面，杜绝 DOM 上下文 Stale 销毁）
                screen_candidates: list[str] = []
                for cell in user_cells:
                    try:
                        if hasattr(cell, "evaluate"):
                            if not await cell.evaluate("el => el.isConnected"):
                                continue
                    except Exception:
                        continue

                    user_link = await cell.query_selector('a[href^="/"][role="link"]')
                    if not user_link:
                        continue

                    href = await user_link.get_attribute("href") or ""
                    cand_handle = href.strip("/").split("/")[0].split("?")[0].lstrip("@")

                    if not cand_handle or cand_handle.lower() == getattr(self, "my_username", "").lower():
                        continue

                    cand_lower = cand_handle.lower()
                    if cand_lower in processed_handles:
                        continue

                    # 1. 锁推私密号识别
                    try:
                        if hasattr(cell, "query_selector") and await cell.query_selector('svg[data-testid="icon-lock"]'):
                            self._print(f"  └─ 🔒 [前置跳过] 粉丝 @{cand_handle} 为私密锁推号，跳过")
                            processed_handles.add(cand_lower)
                            continue
                    except Exception:
                        pass

                    # 2. 检查是否已关注 / 申请中
                    unfollow_btn = await cell.query_selector('button[data-testid$="-unfollow"], button[data-testid*="pending"], button[data-testid*="-cancel"]')
                    if unfollow_btn:
                        processed_handles.add(cand_lower)
                        continue

                    # 3. 提取 UserCell 初步文本并核验黑名单（Phase 1: 毫秒级初筛）
                    cell_text = await cell.inner_text() if hasattr(cell, "inner_text") else ""
                    lines = [l.strip() for l in cell_text.split("\n") if l.strip()]
                    display_name = lines[0] if lines else cand_handle
                    bio_snippet = " ".join(lines[2:]) if len(lines) > 2 else ""

                    if getattr(config, "blacklist_filter_enabled", True) and getattr(config, "blacklist_words", None):
                        cell_user_data = {
                            "display_name": display_name,
                            "screen_name": cand_handle,
                            "bio": bio_snippet,
                            "recent_tweets": [],
                        }
                        is_cell_bl, cell_hit_word = check_user_blacklist(
                            cell_user_data,
                            config.blacklist_words,
                            config.check_options,
                        )
                        if is_cell_bl:
                            self._print(f"  └─ 🚫 [黑名单一票否决] 粉丝 @{cand_handle} 名片命中敏感词 '{cell_hit_word}'，严禁操作，跳过")
                            processed_handles.add(cand_lower)
                            if getattr(self, "history_pool", None):
                                self.history_pool.mark_visited(self.tag, cand_lower)
                            continue

                    # 4. 账号基础过滤（蓝勾认证等）
                    cell_verified = False
                    if hasattr(cell, "query_selector"):
                        try:
                            cell_verified = (await cell.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
                        except Exception:
                            pass
                    is_bad, bad_reason = AccountFilterGuard.check_candidate_account(
                        name=display_name,
                        handle=cand_handle,
                        card_or_dom_text=cell_text,
                        has_verified_badge=cell_verified,
                        config=config,
                    )
                    if is_bad:
                        self._print(f"  └─ ⏩ 跳过粉丝 @{cand_handle}: {bad_reason}")
                        processed_handles.add(cand_lower)
                        continue

                    # 5. 本地去重历史
                    if getattr(self, "history_pool", None) and self.history_pool.is_visited(self.tag, cand_lower):
                        processed_handles.add(cand_lower)
                        continue

                    # 初筛通过，加入待互动名单
                    screen_candidates.append(cand_handle)
                    processed_handles.add(cand_lower)

                found_new_candidate = len(screen_candidates) > 0

                # 第二阶段：依次访问当前视口初筛通过的候选人主页，完成 Phase 2 终审与建联互动
                for cand_handle in screen_candidates:
                    if (
                        self._creator_scanned_counts.get(creator_key, 0) >= config.max_followers_per_target
                        or exec_count >= batch_limit
                        or (current_total_exec + exec_count) >= config.daily_task_limit
                    ):
                        break

                    cand_lower = cand_handle.lower()

                    # 补齐关键防风控锁：工作室云端认领独占锁（防止矩阵多账号撞车同一粉丝）
                    allowed, claim_reason = await self._cloud_claim_target(cand_handle, config)
                    if not allowed:
                        self._print(f"  └─ ☁️ [云端去重] 粉丝 @{cand_handle} 已被矩阵其他账号认领，跳过")
                        continue

                    # 访问粉丝个人主页深度抓取推文并做 Phase 2 完整终审与互动
                    self._print(f"  └─ 🚶 拜访粉丝 @{cand_handle} 主页（准备核验推文与执行互动）...")
                    try:
                        await self._safe_goto(page, f"https://x.com/{cand_handle}", timeout=15000)
                        await asyncio.sleep(random.uniform(2.0, 3.2))
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception as goto_err:
                        self._print(f"  └─ ⚠️ 无法打开粉丝 @{cand_handle} 主页: {goto_err}")
                        await self._safe_go_back(page)
                        continue

                    # 深度互动执行：内部已包含 Phase 2 (Profile Header, Bio, Recent Tweets) 的 check_user_blacklist 一票否决
                    p_followed, p_likes = await self._interact_on_profile_page_simple(page, cand_handle)

                    # 累加该目标博主已处理粉丝计数
                    self._creator_scanned_counts[creator_key] = self._creator_scanned_counts.get(creator_key, 0) + 1

                    if p_followed or p_likes > 0:
                        if p_followed:
                            follows_count += 1
                            consecutive_follows += 1
                        likes_count += p_likes
                        exec_count += 1

                        # 云端去重确认
                        await self._cloud_confirm_target(cand_handle, config)

                        # 动作与频控延时区间
                        min_delay = max(1.0, float(getattr(config, "action_min_delay", 4.0)))
                        max_delay = max(min_delay, float(getattr(config, "action_max_delay", 8.0)))
                        action_delay = random.uniform(min_delay, max_delay)
                        self._print(f"  └─ ⏱️ [频控休整] 随机等待 {action_delay:.1f} 秒 (设定区间: {min_delay}~{max_delay}s)...")
                        await self._sleep_with_control(action_delay)

                    # 原地平滑回退至博主粉丝列表 (利用 SPA 缓存保持滚动高度与 DOM 渲染)
                    try:
                        went_back = await self._safe_go_back(page)
                        curr_url = str(getattr(page, "url", "") or "").lower()
                        if not went_back or f"/{creator_handle.lower()}/followers" not in curr_url:
                            self._print(f"  └─ 🧭 回退未对准粉丝列表，校准重定位至: @{creator_handle}/followers")
                            await self._safe_goto(page, target_followers_url, timeout=12000)
                            await asyncio.sleep(random.uniform(1.8, 2.5))
                            if accumulated_scroll_px > 400:
                                self._print(f"  └─ 📜 恢复粉丝列表浏览深度 (~{accumulated_scroll_px}px)...")
                                recovered = 0
                                while recovered < accumulated_scroll_px:
                                    step = min(random.randint(320, 500), accumulated_scroll_px - recovered)
                                    await self._scroll_with_control(page, distance=step)
                                    recovered += step
                                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except Exception:
                        pass

                # 第三阶段：滚屏与触底检测（彻底根治死循环滚动）
                if not found_new_candidate:
                    empty_scrolls += 1

                    # 探测页面当前滚动高度
                    prev_scroll_h = 0
                    try:
                        if hasattr(page, "evaluate"):
                            prev_scroll_h = await page.evaluate("() => document.body.scrollHeight")
                    except Exception:
                        pass

                    scroll_dist = random.randint(380, 520)
                    await self._scroll_with_control(page, distance=scroll_dist)
                    accumulated_scroll_px += scroll_dist
                    await asyncio.sleep(random.uniform(2.0, 3.0))

                    curr_scroll_h = 0
                    try:
                        if hasattr(page, "evaluate"):
                            curr_scroll_h = await page.evaluate("() => document.body.scrollHeight")
                    except Exception:
                        pass

                    # 触底双重判定：连续 4 次滚屏无新候选人，且页面高度停止增长（已到底部）
                    if empty_scrolls >= 4 and (curr_scroll_h == prev_scroll_h or curr_scroll_h == 0):
                        self._print(f"ℹ️ 博主 @{creator_handle} 的粉丝已全部探寻完毕（页面触底且无新内容），切换至下一目标博主")
                        self._target_creator_idx = (curr_idx + 1) % len(target_creators)
                        break
                else:
                    empty_scrolls = 0
                    scroll_dist = random.randint(280, 450)
                    await self._scroll_with_control(page, distance=scroll_dist)
                    accumulated_scroll_px += scroll_dist
                    await asyncio.sleep(random.uniform(1.5, 2.2))

            # 若当前博主达到配额，推进到下一个博主
            if self._creator_scanned_counts.get(creator_key, 0) >= config.max_followers_per_target:
                self._print(f"✅ 目标博主 @{creator_handle} 达到单博主扫描上限 ({config.max_followers_per_target} 人)，成功完成！")
                self._target_creator_idx = (curr_idx + 1) % len(target_creators)
                await self._maybe_purge_renderer_memory(page, force=True)

        return exec_count, likes_count, follows_count, views_count

    async def run(self, custom_config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = AutomationConfig.from_mapping(custom_config)
        self.tag = config.account_tag
        self._active_config = config
        self._comments_total = 0

        if config.daily_tasks_used >= config.daily_task_limit:
            return {
                "status": "SKIPPED",
                "read_only": True,
                "reason": "DAILY_TASK_LIMIT_REACHED",
                "daily_tasks_used": config.daily_tasks_used,
                "daily_task_limit": config.daily_task_limit,
                "likes": 0, "like_count": 0, "likes_today": 0,
                "follows": 0, "follow_count": 0, "follows_today": 0,
                "comments": 0, "scanned_posts": 0,
            }

        jitter_delay = random.uniform(2.0, 4.0)
        self._print(f"⏳ 注入拟人启动延迟: {jitter_delay:.2f} 秒...")
        await asyncio.sleep(jitter_delay)

        # 💡 1. 拆分并解析多关键词（支持中英文逗号分隔）
        raw_kw = config.keyword
        keywords_list = [k.strip() for k in re.split(r'[,，]', raw_kw) if k.strip()]

        self._log("started", keyword=config.keyword)
        self._print("==========================================")
        self._print(f"老谷控制中心 2026 协同引擎启动! [CDP端口: {self.personality.port}]")
        if keywords_list:
            self._print(f"目标关键词列表 ({len(keywords_list)} 个): {keywords_list} | 全天目标上限: {config.daily_task_limit} 人")
        else:
            self._print(f"🏠 拓客信息流来源: X 首页推荐流 (为你推荐) | 未指定检索词 | 全天目标上限: {config.daily_task_limit} 人")
        if config.schedule_mode == "immediate":
            self._print("⚡ [运行模式: 立即执行] 不受时段限制，立即全力全速执行")
        elif config.schedule_mode == "scheduled":
            self._print("⏱️ [运行模式: 自定义定时] 定时计划任务触发执行")
        else:
            self._print("⏰ [运行模式: 3时段智能分配] 08-12点(35%) | 14-18点(25%含14-16与16-18) | 18-22点(40%) 动态平摊执行")
        if config.cloud_dedup_enabled and config.cloud_dedup_studio_token:
            token_display = config.cloud_dedup_studio_token[:6] + "***" if len(config.cloud_dedup_studio_token) > 6 else "***"
            self._print(f"👥 [工作室协同去重] 已激活 (协同码: {token_display} | 节点: {config.cloud_dedup_device_name})")
        if config.outreach_mode == "keyword_chain":
            depth_desc = "无限制 (持续裂变直到达成配额)" if config.is_chain_infinite else f"{config.max_chain_depth} 层"
            self._print(f"🔗 [拓客工作模式: 关键词种子·无限顺藤摸瓜] (A➜B➜C 链式裂变 | 链条深度: {depth_desc})")
        elif config.outreach_mode == "target_followers":
            targets_desc = f"{len(config.target_creators)} 位博主" if config.target_creators else "未指定目标"
            self._print(f"👥 [拓客工作模式: 扫博主粉丝模式] (批量深挖目标博主粉丝群 | 目标: {targets_desc} | 单博主上限: {config.max_followers_per_target} 人)")
        elif config.chain_hop_enabled:
            depth_desc = "无限制" if config.max_chain_depth == 0 else f"{config.max_chain_depth} 层"
            self._print(f"🌟 [顺藤摸瓜关系网] 已就绪 (递归限深: {depth_desc} | 触发率: {int(config.chain_hop_ratio * 100)}%)")
        else:
            self._print("🌟 [顺藤摸瓜关系网] 未开启 (仅执行推文流直接拓客)")
        if config.periodic_search_refresh_enabled:
            self._print(f"🔄 [定时刷新最新推文] 已开启 (每 {config.search_refresh_interval_minutes} 分钟重新检索刷新，锁定最新发布内容)")
        else:
            self._print("🔄 [定时刷新最新推文] 未开启 (保持默认顺流滚动翻页逻辑)")
        if config.blacklist_filter_enabled and config.blacklist_words:
            self._print(f"🛡️ [敏感词与黑名单过滤] 已开启 (全字段一票否决 · 加载 {len(config.blacklist_words)} 个特征词)")
        else:
            self._print("⚪ [敏感词与黑名单过滤] 已停用 (不过滤敏感词，0 拦截)")
        if getattr(config, "natural_roaming_enabled", True):
            self._print("🛋️ [高阶拟人·自然摸鱼] 已开启 (批次间切回首页摸鱼 15~25 秒，注入行为噪声打乱指纹)")
        if getattr(config, "pre_click_guard_enabled", True):
            self._print("🔍 [卡片质检·前置排雷] 已开启 (点击前毫秒级拦截默认头像与纯数字乱码号，节省配额)")
        if getattr(config, "smart_newbie_recognition_enabled", True):
            self._print("🌱 [新人智能识别] 已开启 (智能吸纳带系统随机数字的高价值真实新手，仅在重叠默认头像/广告时拦截)")
        else:
            self._print("⚪ [新人智能识别] 未开启 (严格模式：一票否决所有含 7 位以上连续数字后缀的用户名)")
        if getattr(config, "search_pagination_refresh_enabled", True):
            self._print("🔄 [最新流自愈·深滚重置] 已开启 (深滚达限或断流自动平滑回顶刷新，防卡死空白)")
        if getattr(config, "graphql_scout_filter_enabled", True):
            self._print("⚡ [底层透视·极速预筛] 已开启 (拦截 SearchTimeline 数据流，内存毫秒级质检过滤，靶向命中高价值博主)")
        else:
            self._print("⚪ [底层透视·极速预筛] 未开启 (保持常规推文卡片 DOM 悬停探测流)")
        if getattr(config, "block_video_streams", True):
            self._print("⚡ [极速省流·防卡加速] 已开启 (底层拦截大体积视频流预加载，降低 60% 内存与显存占用)")
        else:
            self._print("🎥 [多媒体模式] 保持推特原版多媒体加载 (视频流阻断未开启)")
        self._print("==========================================\n")

        try:
            from playwright.async_api import async_playwright, Error as PlaywrightError
        except ImportError as exc:
            raise AutomationEngineError("Playwright 未安装") from exc

        total_exec = 0
        total_likes = 0
        total_follows = 0
        total_views = 0
        recovered_from_nav = False

        # 顺藤摸瓜链条状态初始化
        self._chain_stack = []
        self._chain_current_following_handle = ""

        browser = None
        page = None
        resp_listener = None
        dialog_listener = None
        context_page_listener = None

        # 💡 时间窗口与【动态配额上限】计算函数 (统一采用北京/上海时间 UTC+8，完整判定上午、下午及晚间时段)
        def get_time_window_status(current_total: int, daily_limit: int) -> tuple[bool, str, int]:
            return self.evaluate_time_window(current_total, daily_limit)


        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

                # 🛡️ 注入隐形防风控自愈补丁：彻底抹除自动化 webdriver 特征、补全 window.chrome 并中和 Console 诱饵探针
                try:
                    add_init_script = getattr(context, "add_init_script", None)
                    if callable(add_init_script):
                        stealth_script = """
                        try {
                            if ('webdriver' in Navigator.prototype) {
                                delete Navigator.prototype.webdriver;
                            }
                            Object.defineProperty(navigator, 'webdriver', {
                                get: () => undefined,
                                enumerable: false,
                                configurable: true
                            });
                        } catch (e) {}

                        try {
                            if (!window.chrome) {
                                window.chrome = {};
                            }
                            if (!window.chrome.runtime) {
                                window.chrome.runtime = {
                                    OnInstalledReason: { INSTALL: "install", UPDATE: "update", CHROME_UPDATE: "chrome_update", SHARED_MODULE_UPDATE: "shared_module_update" },
                                    OnRestartRequiredReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
                                    PlatformArch: { ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
                                    PlatformNaclArch: { ARM: "arm", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
                                    PlatformOs: { ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" }
                                };
                            }
                        } catch (e) {}

                        // 🛡️ 防风控 Console 诱饵探针中和 (Neutralize Console Getter Traps)
                        // 反爬探针 (DataDome / Arkose / X) 会向 console.debug/log 传入含 getter 的诱饵对象，
                        // CDP 开启时 DevTools 格式化对象会自动触发 getter，从而判定为自动化脚本。
                        try {
                            const nativeToString = Function.prototype.toString;
                            const toStringMap = new WeakMap();

                            Function.prototype.toString = new Proxy(nativeToString, {
                                apply(target, thisArg, args) {
                                    if (toStringMap.has(thisArg)) {
                                        return toStringMap.get(thisArg);
                                    }
                                    return Reflect.apply(target, thisArg, args);
                                }
                            });
                            toStringMap.set(Function.prototype.toString, 'function toString() { [native code] }');

                            function isBait(arg) {
                                if (!arg || typeof arg !== 'object') return false;
                                try {
                                    if (Object.prototype.hasOwnProperty.call(arg, 'toString') || Object.prototype.hasOwnProperty.call(arg, 'valueOf')) {
                                        return true;
                                    }
                                    let curr = arg;
                                    for (let d = 0; d < 2 && curr && curr !== Object.prototype; d++) {
                                        const descs = Object.getOwnPropertyDescriptors(curr);
                                        for (const k of Object.keys(descs)) {
                                            if (typeof descs[k].get === 'function') {
                                                return true;
                                            }
                                        }
                                        curr = Object.getPrototypeOf(curr);
                                    }
                                } catch(e) {}
                                return false;
                            }

                            const consoleMethods = ['debug', 'log', 'info', 'warn', 'dir'];
                            for (const method of consoleMethods) {
                                if (typeof console[method] === 'function') {
                                    const orig = console[method];
                                    const wrapped = new Proxy(orig, {
                                        apply(target, thisArg, args) {
                                            try {
                                                const safeArgs = args.map(a => isBait(a) ? '[Object]' : a);
                                                return Reflect.apply(target, thisArg, safeArgs);
                                            } catch (e) {
                                                return undefined;
                                            }
                                        }
                                    });
                                    toStringMap.set(wrapped, `function ${method}() { [native code] }`);
                                    console[method] = wrapped;
                                }
                            }

                            // 🛡️ 后台静默运行防风控掩护 (Background Focus & Visibility Masquerade)
                            try {
                                if (typeof Document !== 'undefined') {
                                    Object.defineProperty(Document.prototype, 'visibilityState', {
                                        get: () => 'visible',
                                        enumerable: true,
                                        configurable: true
                                    });
                                    Object.defineProperty(Document.prototype, 'hidden', {
                                        get: () => false,
                                        enumerable: true,
                                        configurable: true
                                    });
                                }
                                if (typeof document !== 'undefined' && typeof document.hasFocus === 'function') {
                                    const origHasFocus = document.hasFocus;
                                    const fakeHasFocus = function hasFocus() { return true; };
                                    toStringMap.set(fakeHasFocus, 'function hasFocus() { [native code] }');
                                    document.hasFocus = new Proxy(origHasFocus, {
                                        apply() { return true; }
                                    });
                                    toStringMap.set(document.hasFocus, 'function hasFocus() { [native code] }');
                                }
                            } catch (e) {}
                        } catch (e) {}
                        """
                        init_res = add_init_script(stealth_script)
                        if asyncio.iscoroutine(init_res):
                            await init_res
                except Exception:
                    pass

                page = await self._select_or_open_x_page(context)
                # 🛡️ 启用 CDP 焦点仿真（后台静默运行，无需抢占前台操作系统窗口即可享有原生焦点事件）
                await self._ensure_page_focus_emulation(page)
                # 🛡️ 启用 CDP 动态时区与语言覆盖（对齐代理归属地，防止地域冲突风控）
                await self._ensure_cdp_locale_and_timezone_override(page, config)
                # ⚡ 启用底层非必要大体积视频流阻断（极速省流防卡，释放 60% 内存与显存占用）
                await self._ensure_video_stream_blocking(page, config)
                try:
                    # 🛡️ 智能视口保障：始终沿用真实物理窗口，严禁触发 DevTools 模拟导致 outerWidth < innerWidth 指纹穿帮
                    cur_w = await page.evaluate("() => window.innerWidth")
                    if cur_w and cur_w < 1080:
                        self.logger.debug("当前物理视口宽度为 %s", cur_w)
                except Exception:
                    pass

                # 🛡️ 代理网络连通性探测与保活待机守护（杜绝因代理断网导致任务误退或关闭浏览器）
                net_err = await self._detect_network_proxy_error(page)
                if net_err:
                    recovered = await self._wait_for_network_recovery(page, initial_err=net_err)
                    if not recovered:
                        self._print("🛑 任务已由控制中心安全中止 (网络保活待机期间退出)")
                        return {
                            "status": "CANCELLED", "reason": "USER_STOPPED_DURING_NETWORK_WAIT",
                            "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                        }

                await self._wait_for_login_state_surface(page, timeout_sec=8.0)

                if not await self._check_login_status(page):
                    # 再次排查是否是网络延迟导致未渲染出登录按钮
                    net_err = await self._detect_network_proxy_error(page)
                    if net_err:
                        recovered = await self._wait_for_network_recovery(page, initial_err=net_err)
                        if not recovered:
                            return {
                                "status": "CANCELLED", "reason": "USER_STOPPED_DURING_NETWORK_WAIT",
                                "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                            }
                    if not await self._check_login_status(page):
                        self._print(f"🛑 拦截：账号 [{self.tag}] 未登录！请先在控制中心点击【▶ 运行】打开浏览器窗口并登录账号！")
                        return {
                            "status": "NOT_LOGGED_IN",
                            "error": f"账号 {self.tag} 未登录，请先手动登录 X 账号！",
                            "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                        }

                self._setup_page_listeners(page)

                # 🛡️ 注册 context.on("page") 多标签智能猎杀守卫（白名单放行 X 页面与系统页，秒关外部官网）
                async def _on_new_page_opened(new_p: Any) -> None:
                    try:
                        await asyncio.sleep(0.3)
                        # 🛡️ 核心保活：若浏览器仅存 1 个标签页，绝不关闭，防止杀死整个浏览器进程
                        if context and hasattr(context, "pages") and len(context.pages) <= 1:
                            return
                        new_url = str(getattr(new_p, "url", "") or "").lower()
                        # 智能白名单放行：推特自身页面、DevTools、chrome内部协议、网络错误页、空白页放行保留
                        if (self._is_x_page_url(new_url) or 
                            new_url.startswith("devtools://") or 
                            new_url.startswith("chrome://") or 
                            new_url.startswith("chrome-error://") or 
                            new_url == "" or 
                            new_url == "about:blank"):
                            return
                        # 捕杀外部第三方官网/广告跳转页面
                        url_display = (new_url[:55] + "...") if len(new_url) > 55 else new_url
                        self._print(f"🛡️ [外链猎杀] 捕获到非 X 外部跳转页面 ({url_display})，自动秒关并保持主页焦点...")
                        try:
                            await new_p.close()
                        except Exception:
                            pass
                        if page and not getattr(page, "is_closed", lambda: False)():
                            await self._ensure_page_focus_emulation(page)
                    except Exception as p_err:
                        self.logger.debug("新标签页处理非阻断提示: %s", p_err)

                context_page_listener = lambda p: asyncio.create_task(_on_new_page_opened(p))
                if hasattr(context, "on"):
                    context.on("page", context_page_listener)

                # 🛡️ 注入轻量防跳出脚本，改写 window.open 防止脚本弹窗
                try:
                    if hasattr(page, "add_init_script"):
                        await page.add_init_script("""
                            try {
                                const _origOpen = window.open;
                                window.open = function(url, target, features) {
                                    if (url && (url.startsWith('/') || url.includes('x.com') || url.includes('twitter.com'))) {
                                        return _origOpen.apply(this, arguments);
                                    }
                                    console.warn('[Automation Guard] Blocked external popup:', url);
                                    return null;
                                };
                            } catch(e) {}
                        """)
                except Exception:
                    pass

                await self._assert_no_challenge(page)
                page_ready = await self._wait_for_page_ready(page, timeout_sec=10.0)
                if not page_ready:
                    # 检查是否断网/代理异常
                    net_err = await self._detect_network_proxy_error(page)
                    if net_err:
                        recovered = await self._wait_for_network_recovery(page, initial_err=net_err)
                        if not recovered:
                            return {
                                "status": "CANCELLED", "reason": "USER_STOPPED_DURING_NETWORK_WAIT",
                                "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                            }
                        page_ready = await self._wait_for_page_ready(page, timeout_sec=10.0)
                    else:
                        self._print("⚠️ 页面初次加载卡顿，启动 3 秒平滑缓冲自愈重试...")
                        await asyncio.sleep(3.0)
                        page_ready = await self._wait_for_page_ready(page, timeout_sec=8.0)
                        if not page_ready:
                            net_err = await self._detect_network_proxy_error(page)
                            if net_err:
                                recovered = await self._wait_for_network_recovery(page, initial_err=net_err)
                                if not recovered:
                                    return {
                                        "status": "CANCELLED", "reason": "USER_STOPPED_DURING_NETWORK_WAIT",
                                        "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                                    }
                            else:
                                self._print("🛑 网页加载卡顿/未正常渲染，准备平滑重试...")
                                return {
                                    "status": "PAGE_NOT_READY",
                                    "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                                }

                if config.cold_start_warmup_enabled:
                    await self._do_cold_start_warmup(page, config)

                # 🧹 关注比例智能维护：巡检并优雅取关超期未回关博主
                if config.smart_unfollow_enabled and not getattr(self, "_cleaned_unreciprocated_today", False):
                    await self._clean_unreciprocated_follows(page, config)

                batch_index = 1

                while total_exec < config.daily_task_limit:
                    current_keyword = keywords_list[(batch_index - 1) % len(keywords_list)] if keywords_list else ""

                    # 💡 校验时间窗口并获取当前窗口的【阶段目标上限】
                    if config.bypass_time_window:
                        is_allowed = True
                        if not config.smart_schedule_enabled:
                            win_desc = "全时段全速运行"
                        else:
                            win_desc = "立即执行" if config.schedule_mode == "immediate" else ("微批次轮换" if config.single_batch_mode else "自定义定时")
                        stage_limit = config.daily_task_limit

                        # 提示时段状态
                        tz = timezone(timedelta(hours=8))
                        now = datetime.now(tz)
                        sh_hour = now.hour
                        is_work_hour = (8 <= sh_hour < 12) or (14 <= sh_hour < 18) or (18 <= sh_hour < 22)
                        if batch_index == 1:
                            if not config.smart_schedule_enabled:
                                self._print(
                                    f"⚡ [智能作息已关闭 (当前时间: {now.strftime('%H:%M')})] "
                                    f"已关闭智能作息保护，全天候全速执行，跳过中午午休与深夜休眠，立即开展拓客！"
                                )
                            elif not is_work_hour:
                                self._print(
                                    f"⚠️ [非工作时段提醒 (当前时间: {now.strftime('%H:%M')})] "
                                    f"提示：当前处于非标准工作时段；因已选择【{win_desc}】模式，跳过时段待机限制，继续正常执行拓客操作！"
                                )
                    else:
                        is_allowed, win_desc, stage_limit = get_time_window_status(total_exec, config.daily_task_limit)

                    if not is_allowed:
                        self._print(f"🌙 [{win_desc}] 触发待机等待，每 10 分钟自动检测下一阶段...")
                        await self._sleep_with_control(600)  # 每 10 分钟检测一次，秒级响应暂停/取消
                        continue

                    kw_disp = f"当前轮询关键词: '{current_keyword}'" if current_keyword else "信息流来源: X 首页推荐流"
                    self._print(f"\n🚀 当前处于 [{win_desc}] - 开始第 {batch_index} 批次任务 | {kw_disp}")
                    self._print(f"📊 阶段进度: {total_exec}/{stage_limit} (全天总目标: {config.daily_task_limit})...")

                    # 动态计算当前批次的目标限制，防止跨越阶段配额上限，并完全透传所有用户自定义配置
                    batch_max = min(stage_limit - total_exec, config.daily_task_limit - total_exec)
                    batch_config = AutomationConfig.from_mapping({
                        **(custom_config or {}),
                        "keyword": current_keyword,
                        "daily_task_limit": total_exec + batch_max
                    })

                    try:
                        if config.outreach_mode == "target_followers":
                            e_cnt, l_cnt, f_cnt, v_cnt = await self._run_target_followers_batch(
                                page,
                                batch_config,
                                total_exec,
                                total_likes,
                                total_follows,
                                total_views,
                            )
                        elif config.outreach_mode == "keyword_chain":
                            e_cnt, l_cnt, f_cnt, v_cnt = await self._run_keyword_chain_fission_batch(
                                page,
                                batch_config,
                                total_exec,
                                total_likes,
                                total_follows,
                                total_views,
                            )
                        else:
                            curr_u = str(getattr(page, "url", "")).lower()
                            seed_h = str(getattr(self, "_in_situ_seed_handle", "") or "").lower()
                            is_in_situ_resume = (
                                getattr(self, "_in_situ_list_active", False)
                                and getattr(config, "in_situ_rest_enabled", True)
                                and getattr(config, "graphql_scout_filter_enabled", True)
                                and getattr(config, "chain_hop_enabled", True)
                                and bool(seed_h)
                                and (f"/{seed_h}/followers" in curr_u or f"/{seed_h}/following" in curr_u)
                            )
                            if is_in_situ_resume:
                                self._print(f"🌅 [原位自然唤醒·透视深度裂变] 无需重新导航，直接在 @{self._in_situ_seed_handle} 关系网列表中排查下一批同好...")
                                c_e, c_l, c_f, c_v = await self._chain_hop_following_exploration(
                                    page,
                                    self._in_situ_seed_handle,
                                    batch_config,
                                    current_chain_depth=1,
                                    current_total_exec=total_exec,
                                    remaining_batch_budget=batch_max,
                                )
                                e_cnt, l_cnt, f_cnt, v_cnt = c_e, c_l, c_f, c_v
                            else:
                                self._in_situ_list_active = False
                                await self.navigate_to_keyword_search(page, current_keyword)
                                e_cnt, l_cnt, f_cnt, v_cnt = await self._run_single_batch(
                                    page,
                                    batch_config,
                                    total_exec,
                                    total_likes,
                                    total_follows,
                                    total_views,
                                )
                    except CONTROL_EXCEPTION_TYPES:
                        raise
                    except PlaywrightError as b_err:
                        b_err_msg = str(b_err).lower()
                        if "execution context was destroyed" in b_err_msg or ("navigation" in b_err_msg and "timeout" not in b_err_msg):
                            self._print(f"⚠️ [换页自愈] 批次执行遭遇页面瞬时跳转碰撞 ({b_err})，自动稳健恢复，进入批次冷却...")
                            recovered_from_nav = True
                            e_cnt, l_cnt, f_cnt, v_cnt = 0, 0, 0, 0
                        elif self._is_crash_error(b_err) or getattr(self, "_is_page_crashed", False):
                            self._print(f"⚠️ [内存溢出自愈] 批次捕获到标签页崩溃 (Out of Memory: {b_err})，正在启动无损自愈重建...")
                            try:
                                page = await self._recover_crashed_page(context, page, fallback_url="https://x.com/home")
                                self._is_page_crashed = False
                                recovered_from_nav = True
                                e_cnt, l_cnt, f_cnt, v_cnt = 0, 0, 0, 0
                                self._print("✅ [自愈成功] 全新标签页就绪，内存已彻底重置归零，继续推进后续任务...")
                            except Exception as rec_err:
                                self._print(f"❌ 标签页崩溃自愈失败: {rec_err}")
                                raise
                        else:
                            raise
                    total_exec += e_cnt
                    total_likes += l_cnt
                    total_follows += f_cnt
                    total_views += v_cnt

                    # 🛡️ 若批次内部捕获到崩溃并暂存了部分已完成数据，在此安全执行标签页重建
                    if getattr(self, "_is_page_crashed", False):
                        self._print("⚠️ [内存溢出自愈] 批次内标记崩溃已安全截流，正在为下一批次无损重建干净标签页...")
                        try:
                            page = await self._recover_crashed_page(context, page, fallback_url="https://x.com/home")
                            self._is_page_crashed = False
                            self._print("✅ [自愈成功] 崩溃标签页已无损重置，渲染内存彻底归零！")
                        except Exception as rec_err:
                            self._print(f"❌ 标签页崩溃自愈失败: {rec_err}")

                    if config.single_batch_mode:
                        self._print(f"🔄 [微批次模式] 单批次执行完成 (本轮共达成 {total_exec} 人)，交接给轮换调度器...")
                        break

                    if total_exec >= config.daily_task_limit:
                        self._print(f"🎉 已达到全天任务上限 ({total_exec}/{config.daily_task_limit})，全天自动化完美收官！")
                        break

                    is_in_situ_rest = (
                        getattr(self, "_in_situ_list_active", False)
                        and getattr(config, "in_situ_rest_enabled", True)
                        and getattr(config, "graphql_scout_filter_enabled", True)
                        and getattr(config, "chain_hop_enabled", True)
                    )
                    if is_in_situ_rest:
                        self._print(f"🏠 [原位自然休眠·透视深度裂变] 博主 @{self._in_situ_seed_handle} 列表仍有待收割同好，保持在列表页原地休眠，彻底消除跨页面瞬移破绽...")
                    else:
                        self._print("🏠 批次完成：执行轻量级内存与 DOM 泄放，切回 For You 首页休息消痕...")
                        try:
                            # 迫使 Blink 引擎清空当前累积的数万个 DOM 节点与媒体解码缓存
                            await page.goto("about:blank", timeout=5000)
                            await asyncio.sleep(0.5)
                            await self._maybe_purge_renderer_memory(page, force=True)
                            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=8000)
                        except Exception:
                            try:
                                await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=8000)
                            except Exception:
                                pass

                    # 批次间隔休息：若本批次未产生关注/互动(0动作0风控负荷)，进入1分钟极速轮转，无需无谓等待
                    if e_cnt == 0:
                        cooldown_min = 1
                        micro_wait = random.randint(3, 8)
                        preset_label = "⚡ 零负荷快速轮转"
                        self._print(
                            "💡 [智能快速轮转] 本批次扫描的博主均已被门槛或敏感词过滤 (未产生关注动作，0 风控负荷)，"
                            "自动将休息缩短为【1 分钟】快速进入下一批次拓客..."
                        )
                    else:
                        cooldown_min, micro_wait = config.compute_batch_cooldown()
                        preset_label = "⚡ 极速高通量" if config.execution_preset == "turbo" else "🛡️ 拟人安全"

                        # 🛬 单日限额软着陆：若开启且单日总进度达到 85%，自动启动末段平滑降速防风控
                        if config.enable_soft_landing and total_exec >= int(config.daily_task_limit * 0.85):
                            cooldown_min = max(cooldown_min, cooldown_min + 1)
                            self._print(f"🛬 [限额软着陆] 今日进度已达 {total_exec}/{config.daily_task_limit} (>= 85%)，平滑拉长批次休息 (+1 分钟) 防范风控...")

                    self._print(f"⏳ 批次结束：开启【{cooldown_min} 分钟】{preset_label}休息倒计时...")

                    for remain_m in range(cooldown_min, 0, -1):
                        self._print(f"⏱️ [批次倒计时] 距离第 {batch_index + 1} 批次启动还剩 {remain_m} 分钟...")
                        for _ in range(6):
                            await self._sleep_with_control(10)
                            if random.random() < 0.20:
                                try:
                                    await page.mouse.wheel(0, random.choice([-40, 40]))
                                    if config.casual_consumption_enabled and random.random() < 0.25:
                                        await self._random_media_consumption(page)
                                except Exception:
                                    pass

                    if micro_wait > 0:
                        self._print(f"⏱️ [批次微扰动] 附加 {micro_wait} 秒离散随机等待，彻底打散周期特征...")
                        await self._sleep_with_control(micro_wait)

                    batch_index += 1

                return {
                    "status": "SUCCESS",
                    "processed_count": total_exec,
                    "likes": total_likes, "like_count": total_likes, "likes_today": total_likes,
                    "follows": total_follows, "follow_count": total_follows, "follows_today": total_follows,
                    "comments": self._comments_total, "comment_count": self._comments_total,
                    "scanned_posts": total_views, "views": total_views,
                    "url": "https://x.com/home",
                    "recovered_from_navigation": recovered_from_nav,
                }

        except NotLoggedInError as log_err:
            self._print(f"🛑 任务中断: {log_err}")
            return {
                "status": "NOT_LOGGED_IN", "error": str(log_err),
                "processed_count": total_exec, "likes": total_likes, "follows": total_follows,
                "comments": self._comments_total, "scanned_posts": total_views,
            }
        except RateLimitPause as rl_err:
            self._print(f"⏱️ 触碰速率限制或限流保护: {rl_err}")
            return {
                "status": "RATE_LIMITED",
                "error": str(rl_err),
                "seconds": rl_err.seconds,
                "retry_after_seconds": rl_err.seconds,
                "processed_count": total_exec, "likes": total_likes, "follows": total_follows,
                "comments": self._comments_total, "scanned_posts": total_views,
            }
        except AutomationStopped as stop_err:
            self._print(f"🛑 任务已由控制中心安全中止: {stop_err}")
            return {
                "status": "CANCELLED",
                "reason": "USER_CANCELLED",
                "error": str(stop_err),
                "processed_count": total_exec, "likes": total_likes, "follows": total_follows,
                "comments": self._comments_total, "scanned_posts": total_views,
            }
        except CaptchaChallengeDetected as challenge_err:
            return {
                "status": "CHALLENGE_REQUIRED", "processed_count": total_exec, "error": str(challenge_err),
                "url": challenge_err.url, "likes": total_likes, "follows": total_follows,
                "comments": self._comments_total, "scanned_posts": total_views,
            }
        except PlaywrightError as pw_err:
            err_msg = str(pw_err).lower()
            if "execution context was destroyed" in err_msg or ("navigation" in err_msg and "timeout" not in err_msg):
                self._print(f"⚠️ [全局换页自愈] 捕获到页面换页瞬时碰撞 ({pw_err})，自动平滑自愈，避免误关浏览器")
                return {
                    "status": "SUCCESS",
                    "processed_count": total_exec,
                    "likes": total_likes, "like_count": total_likes, "likes_today": total_likes,
                    "follows": total_follows, "follow_count": total_follows, "follows_today": total_follows,
                    "comments": self._comments_total, "comment_count": self._comments_total,
                    "scanned_posts": total_views,
                    "recovered_from_navigation": True,
                }
            if self._is_crash_error(pw_err) or getattr(self, "_is_page_crashed", False):
                self._print(f"⚠️ [全局内存溢出自愈] 任务外层捕获到标签页崩溃 (Out of Memory: {pw_err})，执行保底自愈...")
                try:
                    if context:
                        await self._recover_crashed_page(context, page, fallback_url="https://x.com/home")
                        self._is_page_crashed = False
                        self._print("✅ [全局自愈成功] 崩溃标签页已无损重置为正常状态！")
                        return {
                            "status": "SUCCESS",
                            "processed_count": total_exec,
                            "likes": total_likes, "like_count": total_likes, "likes_today": total_likes,
                            "follows": total_follows, "follow_count": total_follows, "follows_today": total_follows,
                            "comments": self._comments_total, "comment_count": self._comments_total,
                            "scanned_posts": total_views,
                            "recovered_from_crash": True,
                        }
                except Exception as rec_err:
                    self.logger.warning("全局自愈异常: %s", rec_err)
            self._print(f"🚨 CDP 通信异常/浏览器已断开: {pw_err}")
            return {
                "status": "CDP_DISCONNECTED", "processed_count": total_exec, "error": str(pw_err),
                "likes": total_likes, "follows": total_follows,
                "comments": self._comments_total, "scanned_posts": total_views,
            }
        finally:
            try:
                if page:
                    if resp_listener and hasattr(page, "remove_listener"):
                        page.remove_listener("response", resp_listener)
                    if dialog_listener and hasattr(page, "remove_listener"):
                        page.remove_listener("dialog", dialog_listener)
                if context and context_page_listener and hasattr(context, "remove_listener"):
                    context.remove_listener("page", context_page_listener)
                if browser:
                    await browser.disconnect()
            except Exception:
                pass

    def _response_callback(self, response: Any) -> None:
        status = getattr(response, "status", 0)
        url = str(getattr(response, "url", ""))
        if "account/access" in url:
            self._print(f"🚨 防风控警告: 检测到账号访问验证拦截! 目标: {url[:80]}")
            self._risk_reason = "ACCOUNT_ACCESS_CHALLENGE"
        elif status == 429 and any(api_kw in url for api_kw in ["graphql", "CreateFriendship", "FavoriteTweet", "CreateTweet"]):
            self._print(f"🚨 防风控警告: 检测到核心接口 HTTP 429 限流! 目标: {url[:80]}")
            self._risk_reason = "HTTP_429_RATE_LIMITED"

    @classmethod
    def _find_rate_limit(cls, text: str) -> str | None:
        for pattern in cls.RATE_LIMIT_PATTERNS:
            match = pattern.search(text or "")
            if match:
                return match.group(0)
        return None

    @staticmethod
    def _filter_read_only_snapshot(
        text: str,
        *,
        url: str,
        title: str,
        config: AutomationConfig,
    ) -> dict[str, Any]:
        """Preserve the legacy read-only filtering interface for diagnostics."""
        normalized = text.casefold()
        matched = not config.keyword or config.keyword.casefold() in normalized
        numbers = [
            int(value.replace(",", ""))
            for value in re.findall(r"\b\d{1,3}(?:,\d{3})*\b", text)
        ]
        follower_value = numbers[0] if numbers else None
        engagement_value = numbers[1] if len(numbers) > 1 else None
        eligible = matched
        if follower_value is not None:
            eligible = eligible and follower_value <= config.max_follower_threshold
        if engagement_value is not None:
            eligible = eligible and engagement_value <= config.max_engagement_threshold
        return {
            "status": "SUCCESS",
            "read_only": True,
            "matched": matched,
            "eligible": eligible,
            "keyword": config.keyword,
            "follower_value": follower_value,
            "engagement_value": engagement_value,
            "daily_task_limit": config.daily_task_limit,
            "url": url,
            "title": title,
        }

    @staticmethod
    def _parse_followers_from_text(text: str) -> int:
        if not text:
            return -1
        try:
            clean_text = text.replace('\n', ' ').replace(',', '').strip()

            patterns = [
                r"([\d\.]+)\s*([KMkm万]?)\s*(?:位|个)?\s*(?:Followers|关注者|フォロワー)",
                r"(?:关注者|Followers|フォロワー)\s*[:：]?\s*([\d\.]+)\s*([KMkm万]?)",
            ]
            for pat in patterns:
                match = re.search(pat, clean_text, re.I)
                if match:
                    val_str, unit = match.group(1), match.group(2)
                    val = float(val_str)
                    unit_upper = unit.upper()
                    if unit_upper == "K":
                        return int(val * 1000)
                    elif unit_upper == "M":
                        return int(val * 1_000_000)
                    elif unit == "万":
                        return int(val * 10000)
                    return int(val)

            if "Followers" in clean_text or "关注者" in clean_text or "フォロワー" in clean_text:
                match = re.search(r"([\d\.]+)\s*([KMkm万]?)", clean_text, re.I)
                if match:
                    val_str, unit = match.group(1), match.group(2)
                    val = float(val_str)
                    unit_upper = unit.upper()
                    if unit_upper == "K":
                        return int(val * 1000)
                    elif unit_upper == "M":
                        return int(val * 1_000_000)
                    elif unit == "万":
                        return int(val * 10000)
                    return int(val)

        except Exception:
            pass
        return -1

    def _log(self, event: str, **fields: Any) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        self.logger.info("x_automation event=%s cdp=%s %s", event, self.cdp_url, details)


# 导出别名
WebE2ETestEngine = XAutomationEngine
