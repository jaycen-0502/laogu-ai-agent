"""
Playwright CDP engine with Multi-Persona AI Reply, VPS Remote Config Sync,
Bookmark & Retweet Protection, Time-Window Scheduling, Multi-Keyword Rotation,
and Control Center Compatibility.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
from typing import Any
from urllib.parse import quote
import urllib.request

from agent.cloud_dedup import (
    _sync_cloud_claim_target,
    _sync_cloud_confirm_target,
)
from agent.history_pool import PersistentHistoryPool


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
    def __init__(self, seconds: int):
        super().__init__(f"Rate limit detected; pause for {seconds} seconds")
        self.seconds = seconds


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
    daily_task_limit: int = 15
    daily_tasks_used: int = 0
    max_follower_threshold: int = 1000
    max_statuses_threshold: int = 1000
    max_engagement_threshold: int = 10000
    batch_interval_minutes: int = 15
    target_url: str = "https://x.com/home"
    account_tag: str = "默认"
    profile_visit_ratio: float = 0.45
    home_browse_ratio: float = 0.20
    ai_reply_ratio: float = 0.15       # 15% 概率执行 AI 评论回复（可由控制中心传 0.0 关闭）[cite: 4]
    bookmark_ratio: float = 0.25       # 25% 概率执行保存书签[cite: 4]
    retweet_ratio: float = 0.10        # 10% 偶发转推概率[cite: 4]
    chain_hop_enabled: bool = True     # 开启顺藤摸瓜关系网拓客
    chain_hop_ratio: float = 0.70      # 顺藤摸瓜触发率 (70%)
    max_chain_depth: int = 2           # 顺藤摸瓜最大递归深度 (限深2层)
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
    cold_start_warmup_enabled: bool = False
    cold_start_warmup_duration_seconds: int = 45
    casual_consumption_enabled: bool = True

    @property
    def bypass_time_window(self) -> bool:
        return self.schedule_mode in {"immediate", "scheduled"} or self.single_batch_mode

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

        server_url = str(values.get("cloud_dedup_server_url") or values.get("server_url") or "https://api.jaycwl.org").strip().rstrip("/")
        studio_token = str(values.get("cloud_dedup_studio_token") or values.get("studio_token") or "").strip()
        device_name = str(values.get("cloud_dedup_device_name") or values.get("device_name") or os.environ.get("COMPUTERNAME", "")).strip()
        if "cloud_dedup_enabled" in values:
            cloud_dedup_enabled = bool(values["cloud_dedup_enabled"])
        else:
            cloud_dedup_enabled = bool(studio_token)

        return cls(
            keyword=kw,
            daily_task_limit=integer("daily_task_limit", 15, 1, 10_000),
            daily_tasks_used=integer("daily_tasks_used", 0, 0, 10_000),
            max_follower_threshold=integer("max_follower_threshold", 1000, 0, 100_000_000),
            max_statuses_threshold=integer("max_statuses_threshold", 1000, 0, 100_000_000),
            max_engagement_threshold=integer("max_engagement_threshold", 10_000, 0, 100_000_000),
            batch_interval_minutes=integer("batch_interval_minutes", 15, 1, 1440),
            target_url=str(values.get("target_url") or "https://x.com/home").strip()[:500],
            account_tag=tag,
            profile_visit_ratio=ratio("profile_visit_ratio", 0.45),
            home_browse_ratio=ratio("home_browse_ratio", 0.20),
            ai_reply_ratio=ratio("ai_reply_ratio", 0.15),
            bookmark_ratio=ratio("bookmark_ratio", 0.25),
            retweet_ratio=ratio("retweet_ratio", 0.10),
            chain_hop_enabled=boolean("chain_hop_enabled", True),
            chain_hop_ratio=ratio("chain_hop_ratio", 0.70),
            max_chain_depth=integer("max_chain_depth", 2, 1, 3),
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
            cold_start_warmup_enabled=boolean("cold_start_warmup_enabled", False),
            cold_start_warmup_duration_seconds=integer("cold_start_warmup_duration_seconds", 45, 15, 300),
            casual_consumption_enabled=boolean("casual_consumption_enabled", True),
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
        "エロ", "マン凸", "パイ凸", "おふパコ", "セフレ", "18禁", "無修正"
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

        target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

        current_pos = getattr(personality, "current_mouse_pos", None)
        if current_pos and isinstance(current_pos, (tuple, list)) and len(current_pos) == 2:
            start_x, start_y = float(current_pos[0]), float(current_pos[1])
        else:
            start_x = max(10.0, target_x - random.uniform(30, 70))
            start_y = max(10.0, target_y - random.uniform(30, 70))

        steps = personality.mouse_steps
        points = generate_bezier_trajectory((start_x, start_y), (target_x, target_y), steps, viewport)
        for x, y in points:
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.01, 0.025))

        personality.current_mouse_pos = (target_x, target_y)
        await element.hover()
        await asyncio.sleep(random.uniform(0.8, 1.8))
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

    is_down = False
    try:
        moved_ok = await human_move_to_fast(page, element, personality)
        if moved_ok and hasattr(page, "mouse") and hasattr(page.mouse, "down"):
            await page.mouse.down()
            is_down = True
            await asyncio.sleep(random.uniform(personality.press_duration[0], personality.press_duration[1]))
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
    except Exception as err:
        logging.getLogger("laogu-ai-agent.x-automation").debug("human_discrete_scroll 滚动缓冲: %s", err)


class XAutomationEngine:
    RATE_LIMIT_PATTERNS = (
        re.compile(r"rate limit", re.I),
        re.compile(r"too many requests", re.I),
        re.compile(r"请求过于频繁"),
        re.compile(r"操作频率过高"),
        re.compile(r"速率限制"),
    )

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

        # 🛡️ 极致细节：AI API 连续失败计数与熔断标志位[cite: 3, 4]
        self._consecutive_ai_failures = 0
        self._ai_circuit_broken = False
        self._active_target_leases: dict[str, Any] = {}

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
        """带精确时间戳的格式化日志输出"""
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] [账号: {self.tag}] {msg}")

    async def _check_run_control(self) -> None:
        guard = getattr(self, "safety_guard", None)
        if guard is None:
            return
        if bool(getattr(guard, "is_cancelled", lambda: False)()):
            raise AutomationStopped("控制中心已取消当前自动化任务")
        while bool(getattr(guard, "is_paused", lambda: False)()):
            self._print("⏸️ [控制中心] 自动化任务处于暂停状态，等待恢复...")
            await asyncio.sleep(1.0)
            if bool(getattr(guard, "is_cancelled", lambda: False)()):
                raise AutomationStopped("控制中心已取消当前自动化任务")

    async def _sleep_with_control(self, seconds: float) -> None:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            await self._check_run_control()
            interval = min(1.0, remaining)
            await asyncio.sleep(interval)
            remaining -= interval

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
            return str(target)
        except Exception as exc:
            self.logger.info("Automation risk screenshot deferred: %s", exc)
            return ""

    async def _allow_action(self, action: str, target_key: str = "") -> bool:
        await self._check_run_control()
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
                    'button[data-testid="confirmationSheetConfirm"]'
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
                    await human_discrete_scroll(page, distance=random.randint(150, 250))
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    try:
                        await page.go_back()
                    except Exception:
                        pass
        except Exception as e:
            self.logger.debug("通知栏拟人浏览非阻断提示: %s", e)

    async def _random_media_consumption(self, page: Any) -> None:
        """拟人图文大图消费：随机点击推文附图放大欣赏，模拟真人富媒体消费习惯"""
        try:
            images = await page.query_selector_all('div[data-testid="tweetPhoto"] img, div[aria-label="Image"] img')
            if images and random.random() < 0.45:
                target_img = random.choice(images[:4])
                if await is_element_fully_loaded(target_img):
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

            # 4. 15% 概率：顺便看一眼推文大图
            if random.random() < 0.15:
                try:
                    img_el = await article.query_selector('div[data-testid="tweetPhoto"] img, div[aria-label="Image"] img')
                    if img_el and await is_element_fully_loaded(img_el):
                        if await safe_human_click(page, img_el, self.personality):
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
        target_seconds = max(15, min(300, getattr(config, "cold_start_warmup_duration_seconds", 45)))
        self._print("=" * 60)
        self._print(f"🌱 [冷启动预热] 开始真人拟态首页浏览预热（计划持续约 {target_seconds} 秒）...")
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
            await human_discrete_scroll(page, distance=scroll_dist)
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
                    if rate_limit_kw or any(w in toast_text.lower() for w in ["unable to follow", "cannot follow", "limit reached", "限制", "稍后再试"]):
                        self._print(f"🚨 [风控限流拦截] X 平台拦截关注动作: {toast_text}")
                        return False, f"RATE_LIMITED: {toast_text}"

            scope = container or page
            if hasattr(scope, "query_selector"):
                following_btn = await scope.query_selector('button[data-testid$="-unfollow"]')
                if following_btn and (not hasattr(following_btn, "is_visible") or await following_btn.is_visible()):
                    return True, "FOLLOWED"

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

    async def _check_login_status(self, page: Any) -> bool:
        try:
            curr_url = getattr(page, "url", "").lower()
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

    async def _assert_no_challenge(self, page: Any) -> None:
        await self._check_run_control()
        await self._dismiss_modal_dialogs(page)
        if self._risk_reason:
            risk = self._risk_reason
            self._risk_reason = ""
            self._print(f"🚨 触发限流/风险阻断保护: {risk}")
            raise RateLimitPause(1800)

        current_url = getattr(page, "url", "").lower()
        if any(kw in current_url for kw in ["account/access", "challenge", "captcha", "turnstile", "account/locked", "consent_flow"]):
            self._print(f"🚨 风控拦截！检测到人机验证/账号受限 URL: {current_url}")
            await self._capture_risk_snapshot(page, "challenge")
            raise CaptchaChallengeDetected(current_url)

        if "account/suspended" in current_url:
            self._print(f"🚨 账号封禁拦截！检测到账号冻结 URL: {current_url}")
            await self._capture_risk_snapshot(page, "suspended")
            raise AutomationEngineError(f"账号已被冻结 (Suspended): {current_url}")

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
        raw_pages = getattr(context, "pages", [])
        pages = [p for p in raw_pages if not str(getattr(p, "url", "")).startswith("devtools")]
        chosen_page = None
        # 1. 优先复用已经停留在 X / Twitter 域名的现有标签页
        for candidate in pages:
            if self._is_x_page_url(getattr(candidate, "url", "")):
                chosen_page = candidate
                break
        # 2. 若现有标签页停留在 google.com、about:blank 等起始页，直接在当前标签页中导航至 X 首页
        if not chosen_page and pages:
            candidate = pages[0]
            try:
                self._print("🌐 当前标签页未在 X 首页，正在平滑导航至 https://x.com/home ...")
                await candidate.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25000)
                chosen_page = candidate
            except Exception as err:
                self.logger.warning("导航现有页面至 x.com 异常: %s，准备尝试新建标签页...", err)
        # 3. 若无标签页或导航失败，新建标签页并导航
        if not chosen_page:
            chosen_page = await context.new_page()
            try:
                self._print("🌐 正在新标签页中打开 https://x.com/home ...")
                await chosen_page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25000)
            except Exception as err:
                self.logger.warning("新建页面并导航至 x.com 异常: %s", err)

        # 🔒 强制统一视口尺寸为推荐比例 1280x800 (16:10 黄金比例)，确保推特三栏完整布局与悬浮名片定位精准稳定
        try:
            await chosen_page.set_viewport_size({"width": 1280, "height": 800})
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

    async def _safe_goto(self, page: Any, url: str, referer: str = "https://x.com/home", timeout: int = 30000) -> None:
        try:
            await page.goto(url, referer=referer, timeout=timeout)
        except Exception as err:
            self.logger.debug("_safe_goto error: %s", err)

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
            hover_card = await page.query_selector('div[data-testid="HoverCard"], div[role="dialog"][data-testid="HoverCard"]')
            if hover_card and await is_element_fully_loaded(hover_card):
                dwell_focus = random.uniform(0.8, 1.8)
                await asyncio.sleep(dwell_focus)
                return hover_card
        return None

    async def _handle_response_interception(self, response: Any) -> None:
        """带 Content-Type 校验的轻量化 JSON 抓包截获器"""
        try:
            status = getattr(response, "status", 200)
            if status == 429:
                url = getattr(response, "url", "")
                self._print(f"🚨 [HTTP 429] 监测到接口请求被限制 (Too Many Requests): {url}")
                self._risk_reason = "HTTP_429_RATE_LIMITED"
                return

            headers = getattr(response, "headers", {}) or {}
            content_type = headers.get("content-type", "").lower()
            if "application/json" not in content_type:
                return

            url = response.url
            if "UserBy" in url or "HoverCard" in url or "UserDetail" in url or "Viewer" in url or "ProfileSpotlight" in url:
                if status == 200:
                    json_data = await response.json()
                    user_data = json_data.get("data", {}).get("user", {}).get("result", {}) or json_data.get("data", {}).get("viewer", {})
                    legacy = user_data.get("legacy", {})

                    screen_name = legacy.get("screen_name") or user_data.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {}).get("screen_name")
                    followers_count = legacy.get("followers_count")
                    statuses_count = legacy.get("statuses_count")
                    is_verified = bool(legacy.get("verified") or user_data.get("is_blue_verified"))

                    if screen_name and isinstance(followers_count, int):
                        self.user_cache[screen_name.lower()] = {
                            "followers_count": followers_count,
                            "statuses_count": statuses_count if isinstance(statuses_count, int) else -1,
                            "description": legacy.get("description", ""),
                            "name": legacy.get("name", ""),
                            "verified": is_verified,
                        }
        except Exception:
            pass

    async def navigate_to_keyword_search(self, page: Any, keyword: str) -> None:
        clean_keyword = keyword.strip()
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

            if "/explore" in page.url.lower():
                self._print("⚠️ 页面被强制重定向至 /explore，这通常代表当前浏览器未登录账号！")
                is_logged = await self._check_login_status(page)
                if not is_logged:
                    raise NotLoggedInError(self.tag)
            else:
                self._print("✅ 检索页面跳转成功，已切入最新推文流！")

        except NotLoggedInError:
            raise
        except Exception as e:
            self._print(f"⚠️ 页面跳转/渲染等待过程捕获到异常: {e}")

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
            if reply_btn and await safe_human_click(page, reply_btn, self.personality):
                modal_opened = True
                await asyncio.sleep(random.uniform(1.8, 3.0))

                input_box = await page.query_selector('div[data-testid="tweetTextarea_0"]')
                if input_box and await is_element_fully_loaded(input_box):
                    await human_type_text(page, input_box, reply_text)

                    send_btn = await page.query_selector('button[data-testid="tweetButton"]')
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
                    await self._dismiss_modal_dialogs(page)
                    discard_btn = await page.query_selector('button[data-testid="confirmationSheetConfirm"]')
                    if discard_btn and hasattr(discard_btn, "is_visible") and await discard_btn.is_visible():
                        await discard_btn.click()
                except Exception:
                    pass
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

    async def _browse_home_feed(self, page: Any) -> int:
        likes_added = 0
        self._print("🎲 [拟人消痕] 随机切回 For You 首页逛街刷帖...")
        try:
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=12000)
            await asyncio.sleep(random.uniform(3.0, 5.0))

            for _ in range(random.randint(2, 4)):
                await human_discrete_scroll(page, distance=random.randint(400, 800))
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

            await human_discrete_scroll(page, distance=400)
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
                        self._risk_reason = follow_reason

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

            await asyncio.sleep(random.uniform(1.2, 2.0))
            await self._scroll_with_control(page, distance=250)
            await asyncio.sleep(random.uniform(1.5, 2.8))

            # 检测是否为冻结/受限异常账号（防止关注被封账号导致账号信誉受损）
            try:
                page_text = await page.inner_text("body")
                if any(w in page_text for w in ["凍結", "suspended", "アカウントは停止", "存在しません", "doesn’t exist"]):
                    self._print(f"  └─ ⚠️ 博主 @{handle} 账号异常（被冻结/受限/不存在），安全跳过")
                    return False, 0
            except Exception:
                pass

            # 身份特征安全拦截校验（蓝勾/金勾、戏仿、Bot）
            if getattr(self, "_active_config", None):
                try:
                    profile_header = await page.query_selector('div[data-testid="UserProfileHeader_Root"]')
                    header_text = await profile_header.inner_text() if profile_header else ""
                    has_badge = False
                    if profile_header:
                        has_badge = (await profile_header.query_selector('svg[data-testid="icon-verified"], [data-testid="icon-verified"]')) is not None
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
            for u_sel in unfollow_selectors:
                if await page.query_selector(u_sel):
                    self._print(f"  └─ ⏩ 博主 @{handle} 已经处于关注或申请中状态，跳过")
                    already_following = True
                    break

            if already_following:
                return False, 0

            # 1. 扫描博主主页推文，优先执行点赞（先赞后粉）
            articles = []
            for _ in range(3):
                articles = await page.query_selector_all("article")
                if articles:
                    break
                await self._scroll_with_control(page, distance=300)
                await asyncio.sleep(1.0)

            if articles:
                for idx, art in enumerate(articles[:2]):
                    if idx == 1 and random.random() > 0.60:
                        break
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
                                    self._print(f"  └─ 👍 [先赞后粉 #{idx+1}] 成功点赞了 @{handle} 的最新推文")
                                    dwell = random.uniform(1.5, 3.0)
                                    await asyncio.sleep(dwell)
                    except Exception as err:
                        self.logger.debug("主页推文元素点赞异常: %s", err)
                        continue
            else:
                self._print(f"  └─ ℹ️ 博主 @{handle} 暂无公开推文（潜水读者号），保持关注意向")

            # 2. 点赞完成后，执行关注与状态核验
            follow_btn = await page.query_selector('button[data-testid$="-follow"], button[aria-label*="Follow"], button[aria-label*="フォロー"]')
            profile_target = f"handle:{handle.lower()}"

            if follow_btn and await self._allow_action("follow", profile_target) and await safe_human_click(page, follow_btn, self.personality):
                follow_ok, follow_reason = await self._verify_follow_success(page)
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
                        self._risk_reason = follow_reason
                        raise RateLimitPause(1800, reason=self._risk_reason)

                follow_dwell = random.uniform(1.5, 3.0)
                await asyncio.sleep(follow_dwell)
        except RateLimitPause:
            raise
        except Exception as e:
            self._print(f"  └─ ⚠️ 主页互动轻微异常: {e}")

        return newly_followed, likes

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

        if (
            current_chain_depth > config.max_chain_depth
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

        self._print(f"  └─ 👆 正在从博主 @{start_handle} 主页寻找【正在关注】链接...")
        await asyncio.sleep(random.uniform(1.5, 2.5))

        target_following_url = f"https://x.com/{start_handle}/following"
        following_link = await page.query_selector(f'a[href="/{start_handle}/following"], a[href$="/following"]')
        if not following_link:
            following_link = await page.query_selector('a[href*="/following"]')

        if following_link and await is_element_fully_loaded(following_link):
            await safe_human_click(page, following_link, self.personality)

        await asyncio.sleep(random.uniform(2.0, 3.0))

        if "following" not in getattr(page, "url", "").lower():
            self._print(f"  └─ 🧭 平滑直达博主关注页: @{start_handle}/following...")
            try:
                await self._safe_goto(page, target_following_url, timeout=15000)
                await asyncio.sleep(2.0)
            except Exception as e:
                self._print(f"  └─ ⏩ 博主 @{start_handle} 关注列表未公开/私密，退出顺藤摸瓜 ({e})")
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

        candidate_friends: list[tuple[str, Any]] = []

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
                    candidate_friends.append((handle, user_link))
                    await self._dismiss_hover_card(page)
                    if len(candidate_friends) >= 2:
                        break
                else:
                    if follower_count > config.max_follower_threshold:
                        self._print(f"  └─ ⏩ 跳过好友 @{handle}: 粉丝数 {follower_count} 超过配置上限 ({config.max_follower_threshold})")
                        if getattr(self, "history_pool", None):
                            self.history_pool.mark_visited(self.tag, handle.lower())
                    await self._dismiss_hover_card(page)
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
                        candidate_friends.append((handle, user_link))
                        await self._dismiss_hover_card(page)
                        if len(candidate_friends) >= 2:
                            break
                    else:
                        if follower_count > config.max_follower_threshold:
                            self._print(f"  └─ ⏩ 跳过好友 @{handle}: 粉丝数 {follower_count} 超过配置上限 ({config.max_follower_threshold})")
                            if getattr(self, "history_pool", None):
                                self.history_pool.mark_visited(self.tag, handle.lower())
                        await self._dismiss_hover_card(page)
                except Exception as cell_err:
                    self.logger.debug("第二轮探测 UserCell 节点异常: %s", cell_err)
                    continue

        if not candidate_friends:
            self._print("  └─ ℹ️ 本轮好友列表中均未发现契合素人同好，结束顺藤摸瓜并安全回溯")
        else:
            for cand_idx, (cand_handle, cand_link) in enumerate(candidate_friends):
                await self._assert_no_challenge(page)
                self._print(f"  └─ 🎯 [顺藤建联] 尝试点击同好 ({cand_idx + 1}/{len(candidate_friends)}) @{cand_handle}...")
                click_ok = False
                try:
                    if await is_element_fully_loaded(cand_link):
                        click_ok = await safe_human_click(page, cand_link, self.personality)
                except Exception as click_err:
                    self.logger.debug("点击候选同好 @%s 异常: %s", cand_handle, click_err)
                    click_ok = False

                if not click_ok:
                    self._print(f"  └─ ⚠️ 点击候选同好 @{cand_handle} 失败/元素失效，尝试下一位候选同好...")
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

                if current_chain_depth < config.max_chain_depth and exec_count < remaining_batch_budget:
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
                break

        await self._safe_go_back(page)
        await _ensure_return_to_entry()
        return exec_count, likes_count, follows_count, views_count

    async def execute_task(self, page: Any, keyword: str = "", *, dry_run: bool = False, **kwargs: Any) -> dict[str, Any]:
        custom_config = {"keyword": keyword, "dry_run": dry_run}
        custom_config.update(kwargs)
        return await self.run(custom_config=custom_config)

    async def _run_single_batch(
        self,
        page: Any,
        config: AutomationConfig,
        current_total_exec: int,
        current_total_likes: int = 0,
        current_total_follows: int = 0,
        current_total_views: int = 0,
    ) -> tuple[int, int, int, int]:
        batch_limit = random.randint(3, 5)
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0
        consecutive_follows = 0
        empty_rounds = 0

        # 每个新批次重置 AI 熔断标记，重新给 API 尝试机会
        self._ai_circuit_broken = False
        self._consecutive_ai_failures = 0

        # 会话级去重集合：跨批次保留已处理博主，杜绝重复扫描
        processed_handles = self._session_processed_handles

        for round_idx in range(25):
            await self._assert_no_challenge(page)

            if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                self._print(f"🎉 当前批次目标已处理完成 ({exec_count} 人)，即将进入挂机倒计时...")
                break

            if exec_count > 0 and random.random() < config.home_browse_ratio:
                likes_count += await self._browse_home_feed(page)
                self._report_progress(
                    processed_count=current_total_exec + exec_count,
                    likes=current_total_likes + likes_count,
                    follows=current_total_follows + follows_count,
                    comments=self._comments_total,
                    scanned_posts=current_total_views + views_count,
                )
                await self.navigate_to_keyword_search(page, config.keyword)
                await asyncio.sleep(3.0)
                continue

            if consecutive_follows >= 2:
                follow_nap_time = random.uniform(180.0, 300.0)
                self._print(f"🛑 [关注专项保护] 已连续关注 {consecutive_follows} 个账号，触发挂机 ({follow_nap_time:.1f} 秒)...")
                consecutive_follows = 0
                await self._sleep_with_control(follow_nap_time)

            articles = await page.query_selector_all("article")
            if not articles:
                self._print("⚠️ 当前未扫描到推文，向下滚动刷出更多推文...")
                await human_discrete_scroll(page, distance=500)
                empty_rounds += 1
                continue

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

                article_text = await article.inner_text()
                is_bad_post, post_reason = AccountFilterGuard.is_spam_or_nsfw(article_text)
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

                            self._print(f"🔍 悬停检查博主名片 -> @{handle}...")
                            move_ok = await human_move_to_fast(page, link, self.personality)
                            if not move_ok:
                                break

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

                            is_candidate_bad, p_reason = AccountFilterGuard.check_candidate_account(
                                name=user_info.get("name", ""),
                                handle=handle,
                                card_or_dom_text=user_info.get("description", ""),
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
                                count_desc = f"{follower_count}" if follower_count >= 0 else "动态探测中"
                                self._print(f"🎯 命中目标 @{handle} (粉丝数: {count_desc} <= {config.max_follower_threshold})")

                                # 跨设备云端去重认领 (Cross-Device Cloud Deduplication Claim)
                                allowed, claim_reason = await self._cloud_claim_target(handle, config)
                                if not allowed:
                                    self._print(f"⏩ [云端去重协同] 目标 @{handle} 已被其他设备认领: {claim_reason} (拟人阅读停顿后移开...)")
                                    await asyncio.sleep(random.uniform(1.8, 3.2))
                                    await self._dismiss_hover_card(page)
                                    break

                                await asyncio.sleep(random.uniform(1.5, 3.0))

                                should_visit_profile = (random.random() < config.profile_visit_ratio)
                                did_hop = False

                                if should_visit_profile:
                                    self._print(f"🚶 点击博主昵称，进入 @{handle} 个人主页...")
                                    if await safe_human_click(page, link, self.personality):
                                        await asyncio.sleep(random.uniform(2.5, 4.0))
                                        p_followed, p_likes = await self._interact_on_profile_page_simple(page, handle)
                                        if p_followed:
                                            follows_count += 1
                                            exec_count += 1
                                            consecutive_follows += 1
                                            await self._cloud_confirm_target(handle, config)
                                            if getattr(self, "history_pool", None):
                                                self.history_pool.mark_visited(self.tag, handle.lower())
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

                                        self._print("🔙 深度拜访结束，优先后退恢复搜索推文轨道...")
                                        back_ok = await self._safe_go_back(page)
                                        if not back_ok or "search" not in getattr(page, "url", "").lower():
                                            self._print("⚠️ 无法后退恢复推文列表，平滑重新导航搜索页...")
                                            await self.navigate_to_keyword_search(page, config.keyword)
                                else:
                                    hover_card_check = await page.query_selector('div[data-testid="HoverCard"]')
                                    follow_btn = None
                                    if hover_card_check:
                                        follow_btn = await hover_card_check.query_selector('button[data-testid$="-follow"]')

                                    if not follow_btn:
                                        # 🛡️ 稳健查询：支持名片浮层嵌套与 layers 容器内的关注按钮
                                        follow_btn = await page.query_selector('div[data-testid="HoverCard"] button[data-testid$="-follow"]')

                                    if not follow_btn:
                                        self._print(f"⚠️ 名片内未找到关注按钮，跳过 @{handle}，防止误点侧边栏推荐博主")
                                        await self._dismiss_hover_card(page)
                                        break

                                    profile_target = f"handle:{handle.lower()}"
                                    if follow_btn and await self._allow_action("follow", profile_target) and await safe_human_click(page, follow_btn, self.personality):
                                        follow_ok, follow_reason = await self._verify_follow_success(page, container=hover_card_check)
                                        if follow_ok:
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
                                                    back_ok = await self._safe_go_back(page)
                                                    if not back_ok or "search" not in getattr(page, "url", "").lower():
                                                        await self.navigate_to_keyword_search(page, config.keyword)
                                        else:
                                            self._print(f"⚠️ 关注未能确认 -> @{handle}: {follow_reason}")
                                            if "RATE_LIMITED" in follow_reason:
                                                self._risk_reason = follow_reason
                                            await self._dismiss_hover_card(page)
                                    else:
                                        self._print(f"⚠️ 关注按钮未就绪或被策略拦截，跳过 @{handle}")
                                        await self._dismiss_hover_card(page)

                                self._report_progress(
                                    processed_count=current_total_exec + exec_count,
                                    likes=current_total_likes + likes_count,
                                    follows=current_total_follows + follows_count,
                                    comments=self._comments_total,
                                    scanned_posts=current_total_views + views_count,
                                )

                                cool_mult = 1.2 if did_hop else 1.0
                                cool_down = max(3.0, self.personality.get_cooldown() * cool_mult)
                                self._print(f"⏱️ [{self.personality.p_type}] 降频休息 {cool_down:.1f} 秒...")
                                await self._sleep_with_control(cool_down)
                                await self._dismiss_hover_card(page)
                            else:
                                self._print(f"⏩ [风控拟人防御] 跳过博主 @{handle} (粉丝数: {follower_count} > 门槛: {config.max_follower_threshold}，模拟阅读停顿后移开...)")
                                await asyncio.sleep(random.uniform(1.8, 3.2))
                                await self._dismiss_hover_card(page)
                            break
                if target_found_in_round:
                    break

            if not target_found_in_round:
                empty_rounds += 1
                await human_discrete_scroll(page, distance=500)
                if empty_rounds >= 6:
                    self._print("⚠️ 连续 6 輪未发现新推文，刷新页面...")
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=12000)
                        await asyncio.sleep(4.0)
                    except Exception:
                        pass
                    empty_rounds = 0

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
        if not keywords_list:
            keywords_list = ["#婚活"]

        self._log("started", keyword=config.keyword)
        self._print("==========================================")
        self._print(f"老谷控制中心 2026 协同引擎启动! [CDP端口: {self.personality.port}]")
        self._print(f"目标关键词列表 ({len(keywords_list)} 个): {keywords_list} | 全天目标上限: {config.daily_task_limit} 人")
        if config.schedule_mode == "immediate":
            self._print("⚡ [运行模式: 立即执行] 不受时段限制，立即全力全速执行")
        elif config.schedule_mode == "scheduled":
            self._print("⏱️ [运行模式: 自定义定时] 定时计划任务触发执行")
        else:
            self._print("⏰ [运行模式: 3时段智能分配] 08-12点(35%) | 14-16点(25%) | 18-22点(40%) 动态平摊执行")
        if config.cloud_dedup_enabled and config.cloud_dedup_studio_token:
            token_display = config.cloud_dedup_studio_token[:6] + "***" if len(config.cloud_dedup_studio_token) > 6 else "***"
            self._print(f"👥 [工作室协同去重] 已激活 (协同码: {token_display} | 节点: {config.cloud_dedup_device_name})")
        if config.chain_hop_enabled:
            self._print(f"🌟 [顺藤摸瓜关系网] 已就绪 (递归限深: {config.max_chain_depth} 层 | 触发率: {int(config.chain_hop_ratio * 100)}%)")
        else:
            self._print("🌟 [顺藤摸瓜关系网] 未开启 (仅执行推文流直接拓客)")
        self._print("==========================================\n")

        try:
            from playwright.async_api import async_playwright, Error as PlaywrightError
        except ImportError as exc:
            raise AutomationEngineError("Playwright 未安装") from exc

        total_exec = 0
        total_likes = 0
        total_follows = 0
        total_views = 0

        browser = None
        page = None
        resp_listener = None

        # 💡 时间窗口与【动态配额上限】计算函数 (统一采用北京/上海时间 UTC+8，与客户端保持一致)
        def get_time_window_status(current_total: int, daily_limit: int) -> tuple[bool, str, int]:
            tz = timezone(timedelta(hours=8))
            now = datetime.now(tz)
            hour = now.hour

            remaining_tasks = daily_limit - current_total
            if remaining_tasks <= 0:
                return False, "全天目标已达成", 0

            # 按照 上午35% / 下午25% / 晚间40% 计算各阶段应达到的累计上限
            morning_target = int(daily_limit * 0.35)
            afternoon_target = int(daily_limit * 0.60)
            evening_target = daily_limit

            if 8 <= hour < 12:
                # 如果上午跑满了 35%，则提前休眠等待下午
                if current_total >= morning_target:
                    return False, f"上午配额已达标 ({current_total}/{morning_target})，等待 14 点", 0
                return True, "上午窗口 (08:00 - 12:00)", morning_target

            elif 14 <= hour < 16:
                # 如果下午跑满了 60%（累计），休眠等待晚间
                if current_total >= afternoon_target:
                    return False, f"下午配额已达标 ({current_total}/{afternoon_target})，等待 18 点", 0
                return True, "下午窗口 (14:00 - 16:00)", afternoon_target

            elif 18 <= hour < 22:
                return True, "晚间窗口 (18:00 - 22:00)", evening_target

            else:
                return False, f"非工作时段 (当前时间: {now.strftime('%H:%M')})", 0

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

                page = await self._select_or_open_x_page(context)
                try:
                    await page.set_viewport_size({"width": 1280, "height": 800})
                except Exception:
                    pass
                await self._wait_for_login_state_surface(page, timeout_sec=8.0)

                if not await self._check_login_status(page):
                    self._print(f"🛑 拦截：账号 [{self.tag}] 未登录！请先在控制中心点击【▶ 运行】打开浏览器窗口并登录账号！")
                    return {
                        "status": "NOT_LOGGED_IN",
                        "error": f"账号 {self.tag} 未登录，请先手动登录 X 账号！",
                        "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                    }

                page.on("response", self._response_callback)
                resp_listener = lambda res: asyncio.create_task(self._handle_response_interception(res))
                page.on("response", resp_listener)

                await self._assert_no_challenge(page)
                page_ready = await self._wait_for_page_ready(page, timeout_sec=10.0)
                if not page_ready:
                    self._print("⚠️ 页面初次加载卡顿，启动 3 秒平滑缓冲自愈重试...")
                    await asyncio.sleep(3.0)
                    page_ready = await self._wait_for_page_ready(page, timeout_sec=8.0)
                    if not page_ready:
                        self._print("🛑 网页加载卡顿/未正常渲染，准备平滑重试...")
                        return {
                            "status": "PAGE_NOT_READY",
                            "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                        }

                if config.cold_start_warmup_enabled:
                    await self._do_cold_start_warmup(page, config)

                batch_index = 1
                while total_exec < config.daily_task_limit:
                    current_keyword = keywords_list[(batch_index - 1) % len(keywords_list)]

                    # 💡 校验时间窗口并获取当前窗口的【阶段目标上限】
                    if config.bypass_time_window:
                        is_allowed = True
                        win_desc = "立即执行" if config.schedule_mode == "immediate" else ("微批次轮换" if config.single_batch_mode else "自定义定时")
                        stage_limit = config.daily_task_limit

                        # 超过工作时间选了立即执行，不强制禁止待机，仅在批次首次进入时输出提醒
                        tz = timezone(timedelta(hours=8))
                        now = datetime.now(tz)
                        sh_hour = now.hour
                        is_work_hour = (8 <= sh_hour < 12) or (14 <= sh_hour < 16) or (18 <= sh_hour < 22)
                        if not is_work_hour and batch_index == 1:
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

                    self._print(f"\n🚀 当前处于 [{win_desc}] - 开始第 {batch_index} 批次任务 | 当前轮询关键词: '{current_keyword}'")
                    self._print(f"📊 阶段进度: {total_exec}/{stage_limit} (全天总目标: {config.daily_task_limit})...")

                    await self.navigate_to_keyword_search(page, current_keyword)

                    # 动态计算当前批次的目标限制，防止跨越阶段配额上限，并完全透传所有用户自定义配置
                    batch_max = min(stage_limit - total_exec, config.daily_task_limit - total_exec)
                    batch_config = AutomationConfig.from_mapping({
                        **(custom_config or {}),
                        "keyword": current_keyword,
                        "daily_task_limit": total_exec + batch_max
                    })

                    e_cnt, l_cnt, f_cnt, v_cnt = await self._run_single_batch(
                        page,
                        batch_config,
                        total_exec,
                        total_likes,
                        total_follows,
                        total_views,
                    )
                    total_exec += e_cnt
                    total_likes += l_cnt
                    total_follows += f_cnt
                    total_views += v_cnt

                    if config.single_batch_mode:
                        self._print(f"🔄 [微批次模式] 单批次执行完成 (本轮共达成 {total_exec} 人)，交接给轮换调度器...")
                        break

                    if total_exec >= config.daily_task_limit:
                        self._print(f"🎉 已达到全天任务上限 ({total_exec}/{config.daily_task_limit})，全天自动化完美收官！")
                        break


                    self._print("🏠 批次完成：执行轻量级内存与 DOM 泄放，切回 For You 首页休息消痕...")
                    try:
                        # 迫使 Blink 引擎清空当前累积的数万个 DOM 节点与媒体解码缓存
                        await page.goto("about:blank", timeout=5000)
                        await asyncio.sleep(0.5)
                        try:
                            if hasattr(page, "evaluate"):
                                await page.evaluate("() => { if (window.gc) window.gc(); }")
                        except Exception:
                            pass
                        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=8000)
                    except Exception:
                        try:
                            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=8000)
                        except Exception:
                            pass

                    # 15~25 分钟随机批次间隔，打破定时间隔特征
                    random_interval_min = config.batch_interval_minutes + random.randint(-2, 5)
                    random_interval_min = max(5, random_interval_min)

                    self._print(f"⏳ 批次结束：开启【{random_interval_min} 分钟】拟人随机休息倒计时...")

                    for remain_m in range(random_interval_min, 0, -1):
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

                    batch_index += 1

                return {
                    "status": "SUCCESS",
                    "processed_count": total_exec,
                    "likes": total_likes, "like_count": total_likes, "likes_today": total_likes,
                    "follows": total_follows, "follow_count": total_follows, "follows_today": total_follows,
                    "comments": self._comments_total, "comment_count": self._comments_total,
                    "scanned_posts": total_views, "views": total_views,
                    "url": "https://x.com/home",
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
            self._print(f"🚨 CDP 通信异常/浏览器已断开: {pw_err}")
            return {
                "status": "CDP_DISCONNECTED", "processed_count": total_exec, "error": str(pw_err),
                "likes": total_likes, "follows": total_follows,
                "comments": self._comments_total, "scanned_posts": total_views,
            }
        finally:
            try:
                if page and resp_listener:
                    page.remove_listener("response", resp_listener)
                if browser:
                    await browser.disconnect()
            except Exception:
                pass

    def _response_callback(self, response: Any) -> None:
        status = getattr(response, "status", 0)
        url = str(getattr(response, "url", ""))
        if status in (429, 403) or "account/access" in url:
            self._print(f"🚨 防风控警告: 检测到 HTTP {status} 或验证拦截! 目标: {url[:80]}")
            if status == 429:
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
