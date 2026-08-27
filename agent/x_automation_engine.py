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
import os
import random
import re
import threading
from typing import Any
from urllib.parse import quote
import urllib.request


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

        kw = str(values.get("keyword") or values.get("keywords") or values.get("search_keyword") or "").strip()[:500]
        tag = str(values.get("account_tag") or values.get("profile_name") or values.get("profile_id") or "默认").strip()

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


async def is_element_fully_loaded(element: Any) -> bool:
    if not element:
        return False
    try:
        await element.scroll_into_view_if_needed(timeout=2000)
        if not await element.is_visible():
            return False
        box = await element.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
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

        target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

        start_x = target_x - random.uniform(30, 70)
        start_y = target_y - random.uniform(30, 70)

        steps = personality.mouse_steps
        for index in range(1, steps + 1):
            t = index / steps
            x = start_x + (target_x - start_x) * t
            y = start_y + (target_y - start_y) * t
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.01, 0.025))

        await element.hover()
        await asyncio.sleep(random.uniform(0.8, 1.8))
        return True
    except Exception:
        return False


async def safe_human_click(page: Any, element: Any, personality: ProfilePersonality) -> bool:
    if not await is_element_fully_loaded(element):
        return False

    try:
        moved_ok = await human_move_to_fast(page, element, personality)
        if not moved_ok:
            return False

        await page.mouse.down()
        await asyncio.sleep(random.uniform(personality.press_duration[0], personality.press_duration[1]))
        await page.mouse.up()
        return True
    except Exception:
        pass
    return False


async def human_type_text(page: Any, element: Any, text: str) -> None:
    """模拟真人按键打字，带有随机微延时与节奏变异[cite: 3, 4]"""
    try:
        await element.click()

        # 🛡️ 极致细节：打字前追加 0.6 ~ 1.2 秒“光标闪烁/键盘弹起”微缓冲[cite: 3, 4]
        await asyncio.sleep(random.uniform(0.6, 1.2))

        for i, char in enumerate(text):
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.08, 0.22))
            # 每打 5-7 个字符插入微小的思考停顿，拟真度更高
            if i > 0 and i % random.randint(5, 7) == 0:
                await asyncio.sleep(random.uniform(0.3, 0.6))
        await asyncio.sleep(random.uniform(0.8, 1.5))
    except Exception as err:
        logging.getLogger("laogu-ai-agent.x-automation").debug("human_type_text 打字非阻断提示: %s", err)


async def human_discrete_scroll(page: Any, distance: int | None = None) -> None:
    """模拟真人离散滚轮下翻"""
    try:
        total_dist = distance or random.randint(350, 700)
        scrolled = 0
        while scrolled < total_dist:
            step = random.randint(80, 180)
            amount = min(step, total_dist - scrolled)
            await page.mouse.wheel(0, amount)
            scrolled += amount
            await asyncio.sleep(random.uniform(0.06, 0.15))

        await asyncio.sleep(random.uniform(1.5, 3.5))
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
        self._comments_total = 0

        # 🛡️ 极致细节：AI API 连续失败计数与熔断标志位[cite: 3, 4]
        self._consecutive_ai_failures = 0
        self._ai_circuit_broken = False

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

    async def _check_login_status(self, page: Any) -> bool:
        try:
            curr_url = page.url.lower()
            if "/i/flow/login" in curr_url or "/login" in curr_url:
                return False

            post_btn = await page.query_selector('a[data-testid="SideNav_NewTweet_Button"]')
            profile_link = await page.query_selector('a[data-testid="AppTabBar_Profile_Link"]')

            if post_btn or profile_link:
                return True

            login_btn = await page.query_selector('a[data-testid="loginButton"]')
            if login_btn:
                return False
        except Exception:
            pass
        return True

    async def _assert_no_challenge(self, page: Any) -> None:
        current_url = page.url.lower()
        if any(kw in current_url for kw in ["account/access", "challenge", "captcha", "turnstile"]):
            self._print(f"🚨 风控拦截！检测到人机验证 URL: {current_url}")
            raise CaptchaChallengeDetected(current_url)

        try:
            challenge_frame = await page.query_selector('iframe[src*="challenges.cloudflare.com"], #challenge-stage, iframe[title*="Turnstile"]')
            if challenge_frame:
                self._print("🚨 风控拦截！DOM 结构中检测到 Cloudflare 人机验证框!")
                raise CaptchaChallengeDetected(current_url)
        except Exception:
            pass

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
        return True

    async def _dismiss_hover_card(self, page: Any) -> None:
        try:
            await page.mouse.move(10, 10)
            await asyncio.sleep(0.5)
        except Exception:
            pass

    async def _handle_response_interception(self, response: Any) -> None:
        """带 Content-Type 校验的轻量化 JSON 抓包截获器"""
        try:
            headers = getattr(response, "headers", {}) or {}
            content_type = headers.get("content-type", "").lower()
            if "application/json" not in content_type:
                return

            url = response.url
            if "UserBy" in url or "HoverCard" in url or "UserDetail" in url or "Viewer" in url or "ProfileSpotlight" in url:
                if response.status == 200:
                    json_data = await response.json()
                    user_data = json_data.get("data", {}).get("user", {}).get("result", {}) or json_data.get("data", {}).get("viewer", {})
                    legacy = user_data.get("legacy", {})

                    screen_name = legacy.get("screen_name") or user_data.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {}).get("screen_name")
                    followers_count = legacy.get("followers_count")
                    statuses_count = legacy.get("statuses_count")

                    if screen_name and isinstance(followers_count, int):
                        self.user_cache[screen_name.lower()] = {
                            "followers_count": followers_count,
                            "statuses_count": statuses_count if isinstance(statuses_count, int) else -1,
                            "description": legacy.get("description", ""),
                            "name": legacy.get("name", "")
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

    async def _add_bookmark(self, page: Any, article: Any) -> bool:
        """为目标推文加入【书签（Bookmark）】"""
        try:
            bookmark_btn = await article.query_selector('button[data-testid="bookmark"]')
            if bookmark_btn and await safe_human_click(page, bookmark_btn, self.personality):
                self._print("  └─ 🔖 [高权重社交] 成功将目标推文加入书签（Bookmark）！")
                await asyncio.sleep(random.uniform(1.5, 3.0))
                return True
        except Exception:
            pass
        return False

    async def _do_retweet(self, page: Any, article: Any) -> bool:
        """偶发【转推（Retweet）】目标推文"""
        try:
            retweet_btn = await article.query_selector('button[data-testid="retweet"]')
            if retweet_btn and await safe_human_click(page, retweet_btn, self.personality):
                await asyncio.sleep(random.uniform(1.0, 2.0))
                confirm_retweet = await page.query_selector('div[data-testid="retweetConfirm"]')
                if confirm_retweet and await safe_human_click(page, confirm_retweet, self.personality):
                    self._print("  └─ 🔁 [偶发转推] 成功转推（Retweet）了该推文！")
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                    return True
        except Exception:
            pass
        return False

    async def _do_ai_comment_reply(self, page: Any, article: Any) -> bool:
        """调用 ChatGPT 模型生成回复（熔断保护 + 异步线程解耦）[cite: 3, 4]"""
        # 🛡️ 熔断检查：连续失败达到 3 次时，跳过本批次 AI 评论，仅保留点赞和关注[cite: 3, 4]
        if self._ai_circuit_broken:
            self._print("  └─ ⚡ [熔断保护] AI 接口处于冷却保护状态，跳过本条评论生成")
            return False

        try:
            tweet_text = await article.inner_text()
            reply_text = await asyncio.to_thread(generate_ai_reply, tweet_text)

            # 成功重置连续失败计数
            self._consecutive_ai_failures = 0

            reply_btn = await article.query_selector('button[data-testid="reply"]')
            if reply_btn and await safe_human_click(page, reply_btn, self.personality):
                await asyncio.sleep(random.uniform(1.8, 3.0))

                input_box = await page.query_selector('div[data-testid="tweetTextarea_0"]')
                if input_box and await is_element_fully_loaded(input_box):
                    await human_type_text(page, input_box, reply_text)

                    send_btn = await page.query_selector('button[data-testid="tweetButton"]')
                    if send_btn and await safe_human_click(page, send_btn, self.personality):
                        self._print("  └─ 💬 [ChatGPT 拟人回复] 评论发送成功！")
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                        return True
        except Exception as e:
            self._consecutive_ai_failures += 1
            self._print(f"  └─ ⚠️ AI 评论回复未完成 ({self._consecutive_ai_failures}/3): {e}")

            if self._consecutive_ai_failures >= 3:
                self._ai_circuit_broken = True
                self._print("  └─ 🚨 [熔断触发] AI 接口连续 3 次异常，已暂停本批次评论，防止重复兜底！")
        return False

    async def _like_and_engage_post(self, page: Any, article: Any, config: AutomationConfig) -> int:
        """多维度拟人社交行为组合执行器（带热度感知动态评论过滤）[cite: 3, 4]"""
        likes_added = 0
        first_like_btn = await article.query_selector('button[data-testid="like"]')
        if first_like_btn and await safe_human_click(page, first_like_btn, self.personality):
            self._print("  └─ 👍 成功点赞了目标推文")
            likes_added += 1
            await asyncio.sleep(random.uniform(1.5, 3.0))

        if random.random() < config.bookmark_ratio:
            await self._add_bookmark(page, article)

        if random.random() < config.retweet_ratio:
            await self._do_retweet(page, article)

        # 🛡️ 极致细节：热度感知评论——解析互动量，避免在 0 赞 0 转推的死寂推文下留言[cite: 3, 4]
        if config.ai_reply_ratio > 0.0 and random.random() < config.ai_reply_ratio:
            try:
                article_inner = await article.inner_text()
                # 匹配数字（支持 K/M/万 单位）判定是否有基础互动
                has_engagement = bool(re.search(r'[\d\.]+\s*[KMkm万]?', article_inner))
                if has_engagement or random.random() < 0.30:  # 70% 要求有互动，30% 允许冷门推文
                    if await self._do_ai_comment_reply(page, article):
                        self._comments_total += 1
                else:
                    self._print("  └─ ⏩ [热度感知] 推文属于零互动冷门帖子，跳过评论仅点赞/关注")
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
                        like_btn = await art.query_selector('button[data-testid="like"]')
                        if like_btn and await is_element_fully_loaded(like_btn):
                            if await safe_human_click(page, like_btn, self.personality):
                                self._print("  └─ 随机点赞了推荐页一条推文（模拟真人闲逛）")
                                likes_added += 1
                                await asyncio.sleep(random.uniform(2.0, 4.0))

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
            await human_discrete_scroll(page, distance=400)
            await asyncio.sleep(random.uniform(3.0, 5.0))

            follow_btn = await page.query_selector('button[data-testid$="-follow"]')
            if follow_btn and await safe_human_click(page, follow_btn, self.personality):
                self._print(f"  └─ ➕ [主页关注] 在个人主页成功关注博主 -> @{handle}")
                followed = True
                await asyncio.sleep(random.uniform(2.5, 4.0))

            articles = await page.query_selector_all("article")
            for art in articles[:3]:
                like_btn = await art.query_selector('button[data-testid="like"]')
                if like_btn and await is_element_fully_loaded(like_btn):
                    btn_label = await like_btn.get_attribute("aria-label") or ""
                    if "Liked" not in btn_label and "已赞" not in btn_label and "いいね済" not in btn_label:
                        if await safe_human_click(page, like_btn, self.personality):
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

        except Exception as e:
            self._print(f"⚠️ 主页深度拜访产生异常，执行安全拉回: {e}")
            await self.navigate_to_keyword_search(page, keyword)

        return followed, likes

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

        processed_handles = set()

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
                await asyncio.sleep(follow_nap_time)

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
                    self._print(f"⏩ 过滤推文: 包含{post_reason}")
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

                            self._print(f"🔍 悬停检查博主名片 -> @{handle}...")
                            move_ok = await human_move_to_fast(page, link, self.personality)
                            if not move_ok:
                                break

                            hover_card = None
                            for _ in range(8):
                                await asyncio.sleep(0.35)
                                hover_card = await page.query_selector('div[data-testid="HoverCard"]')
                                if hover_card and await is_element_fully_loaded(hover_card):
                                    break

                            follower_count, statuses_count = await self._get_followers_robust(page, handle, hover_card)

                            user_info = self.user_cache.get(handle.lower(), {})
                            is_profile_bad, p_reason = AccountFilterGuard.is_spam_or_nsfw(f"{user_info.get('name', '')} {user_info.get('description', '')}")
                            if is_profile_bad:
                                self._print(f"⏩ 跳过博主 @{handle}: {p_reason}")
                                await self._dismiss_hover_card(page)
                                break

                            if statuses_count > config.max_statuses_threshold and statuses_count != -1:
                                self._print(f"⏩ 跳过博主 @{handle} (发帖量 {statuses_count} > {config.max_statuses_threshold})")
                                await self._dismiss_hover_card(page)
                                break

                            is_match = (follower_count <= config.max_follower_threshold) if follower_count >= 0 else True

                            if is_match:
                                count_desc = f"{follower_count}" if follower_count >= 0 else "动态探测中"
                                self._print(f"🎯 命中目标 @{handle} (粉丝数: {count_desc} <= {config.max_follower_threshold})")
                                await asyncio.sleep(random.uniform(1.5, 3.0))

                                should_visit_profile = (random.random() < config.profile_visit_ratio)

                                if should_visit_profile:
                                    if await safe_human_click(page, link, self.personality):
                                        await asyncio.sleep(random.uniform(3.0, 5.0))
                                        p_followed, p_likes = await self._interact_on_profile_page(page, handle, config.keyword)
                                        if p_followed:
                                            follows_count += 1
                                            exec_count += 1
                                            consecutive_follows += 1
                                        likes_count += p_likes
                                else:
                                    hover_card_check = await page.query_selector('div[data-testid="HoverCard"]')
                                    follow_btn = None
                                    if hover_card_check and await is_element_fully_loaded(hover_card_check):
                                        follow_btn = await hover_card_check.query_selector('button[data-testid$="-follow"]')

                                    if not follow_btn:
                                        follow_btn = await page.query_selector('button[data-testid$="-follow"]')

                                    if follow_btn and await safe_human_click(page, follow_btn, self.personality):
                                        self._print(f"➕ [名片关注] 自动关注成功 -> @{handle}")
                                        follows_count += 1
                                        exec_count += 1
                                        consecutive_follows += 1

                                        await self._dismiss_hover_card(page)

                                        # 触发包含点赞、书签、转推、ChatGPT 评论的多维拟人社交组合拳
                                        engaged_likes = await self._like_and_engage_post(page, article, config)
                                        likes_count += engaged_likes
                                    else:
                                        self._print(f"⚠️ 关注按钮未就绪，跳过 @{handle}")
                                        await self._dismiss_hover_card(page)

                                self._report_progress(
                                    processed_count=current_total_exec + exec_count,
                                    likes=current_total_likes + likes_count,
                                    follows=current_total_follows + follows_count,
                                    comments=self._comments_total,
                                    scanned_posts=current_total_views + views_count,
                                )

                                cool_down = self.personality.get_cooldown()
                                self._print(f"⏱️ [{self.personality.p_type}] 降频休息 {cool_down:.1f} 秒...")
                                await asyncio.sleep(cool_down)
                                await self._dismiss_hover_card(page)
                            else:
                                self._print(f"⏩ 跳过博主 @{handle} (粉丝数: {follower_count} > 门槛: {config.max_follower_threshold})")
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
        self._print("⏰ [3时段配额分配模式] 08-12点(35%) | 14-16点(25%) | 18-22点(40%) 动态平摊执行")
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

        # 💡 东京时间 (UTC+9) 3 段式允许窗口与【动态配额上限】计算函数
        def get_time_window_status(current_total: int, daily_limit: int) -> tuple[bool, str, int]:
            tokyo_tz = timezone(timedelta(hours=9))
            now_tokyo = datetime.now(tokyo_tz)
            hour = now_tokyo.hour

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
                return False, f"非工作时段 (当前东京时间: {now_tokyo.strftime('%H:%M')})", 0

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

                valid_pages = [p for p in context.pages if not p.url.startswith("devtools")]
                page = valid_pages[0] if valid_pages else await context.new_page()

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
                    self._print("🛑 网页加载卡顿/未正常渲染，准备平滑重试...")
                    return {
                        "status": "PAGE_NOT_READY",
                        "processed_count": 0, "likes": 0, "follows": 0, "comments": 0, "scanned_posts": 0,
                    }

                batch_index = 1
                while total_exec < config.daily_task_limit:
                    current_keyword = keywords_list[(batch_index - 1) % len(keywords_list)]

                    # 💡 校验时间窗口并获取当前窗口的【阶段目标上限】
                    is_allowed, win_desc, stage_limit = get_time_window_status(total_exec, config.daily_task_limit)

                    if not is_allowed:
                        self._print(f"🌙 [{win_desc}] 触发休眠，每 10 分钟自动检测下一阶段...")
                        await asyncio.sleep(600)  # 每 10 分钟检测一次
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

                    if total_exec >= config.daily_task_limit:
                        self._print(f"🎉 已达到全天任务上限 ({total_exec}/{config.daily_task_limit})，全天自动化完美收官！")
                        break

                    self._print("🏠 批次完成：切回 For You 首页休息消痕...")
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
                            await asyncio.sleep(10)
                            if random.random() < 0.20:
                                try:
                                    await page.mouse.wheel(0, random.choice([-40, 40]))
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