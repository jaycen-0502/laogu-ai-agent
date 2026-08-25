"""
Playwright CDP engine with Batch Interval Countdown & Ultra-Humanlike Anti-Ban System.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import os
import random
import re
from typing import Any
from urllib.parse import quote


class AutomationEngineError(RuntimeError):
    """Expected automation failure that should be reported to the task log."""


class CaptchaChallengeDetected(AutomationEngineError):
    """检测到 Cloudflare / X 平台人机验证卡点，必须中断执行以保护账号安全"""
    def __init__(self, url: str):
        super().__init__(f"检测到人机验证页面 (account/access): {url}")
        self.url = url


@dataclass(frozen=True)
class AutomationConfig:
    keyword: str = ""
    daily_task_limit: int = 15
    max_follower_threshold: int = 1000
    max_statuses_threshold: int = 1000
    batch_interval_minutes: int = 15    # 批次间隔时间（分钟）
    target_url: str = "https://x.com/home"
    account_tag: str = "默认"
    profile_visit_ratio: float = 0.45   # 45% 概率进入主页
    home_browse_ratio: float = 0.20      # 20% 概率去推荐页“逛街”

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

        kw = str(values.get("keyword") or values.get("keywords") or values.get("search_keyword") or "").strip()[:500]
        tag = str(values.get("account_tag") or values.get("profile_name") or values.get("profile_id") or "默认").strip()

        return cls(
            keyword=kw,
            daily_task_limit=integer("daily_task_limit", 15, 1, 10_000),
            max_follower_threshold=integer("max_follower_threshold", 1000, 0, 100_000_000),
            max_statuses_threshold=integer("max_statuses_threshold", 1000, 0, 100_000_000),
            batch_interval_minutes=integer("batch_interval_minutes", 15, 1, 1440),
            target_url=str(values.get("target_url") or "https://x.com/home").strip()[:500],
            account_tag=tag,
            profile_visit_ratio=float(values.get("profile_visit_ratio", 0.45)),
            home_browse_ratio=float(values.get("home_browse_ratio", 0.20)),
        )


class ProfilePersonality:
    """账号专属性格基因库，提高冷却时间以避免 Rate Limit"""

    def __init__(self, cdp_url: str):
        port_match = re.search(r":(\d+)", cdp_url)
        self.port = port_match.group(1) if port_match else cdp_url

        hash_val = int(hashlib.md5(cdp_url.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(hash_val)

        self.mean_cool_down = rng.uniform(30.0, 55.0)
        self.std_dev_cool_down = rng.uniform(5.0, 10.0)
        self.mouse_steps = rng.randint(18, 32)
        self.press_duration = (rng.uniform(0.10, 0.22), rng.uniform(0.20, 0.35))
        self.nap_interval_range = (rng.randint(2, 3), rng.randint(3, 5))
        self.nap_duration_mean = rng.uniform(300.0, 500.0)

        if self.mean_cool_down < 35.0:
            self.p_type = "标准自然型"
        else:
            self.p_type = "沉稳慢读型"

    def get_cooldown(self) -> float:
        val = random.gauss(self.mean_cool_down, self.std_dev_cool_down)
        return max(15.0, min(90.0, val))

    def get_nap_duration(self) -> float:
        val = random.gauss(self.nap_duration_mean, 60.0)
        return max(150.0, min(600.0, val))


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


async def human_discrete_scroll(page: Any, distance: int | None = None) -> None:
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
    except Exception:
        pass


class XAutomationEngine:
    def __init__(self, cdp_url: str = "", config_path: str = "selectors_config.json", *, logger: logging.Logger | None = None, **kwargs: Any):
        target_cdp = cdp_url or kwargs.get("cdp_url") or ""
        if not str(target_cdp).strip():
            raise ValueError("cdp_url is required")
        self.cdp_url = str(target_cdp).strip()
        self.logger = logger or logging.getLogger("laogu-ai-agent.x-automation")
        self.tag = "系统"
        self.config_path = config_path
        self.user_cache: dict[str, dict[str, Any]] = {}
        self.personality = ProfilePersonality(self.cdp_url)

    def _print(self, msg: str) -> None:
        """带精确时间戳格式 [HH:MM:SS] 的控制台输出"""
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] [账号: {self.tag}] {msg}")

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
        try:
            main_col = await page.wait_for_selector('div[data-testid="primaryColumn"]', timeout=int(timeout_sec * 1000))
            if main_col and await is_element_fully_loaded(main_col):
                return True
        except Exception:
            pass
        return False

    async def _dismiss_hover_card(self, page: Any) -> None:
        try:
            await page.mouse.move(10, 10)
            await asyncio.sleep(0.5)
        except Exception:
            pass

    async def _handle_response_interception(self, response: Any) -> None:
        try:
            url = response.url
            if "UserBy" in url or "HoverCard" in url or "UserDetail" in url or "Viewer" in url:
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

                        if self.tag and (screen_name.lower() in self.tag.lower() or str(self.tag) in screen_name):
                            try:
                                snapshot_path = os.path.join(os.getcwd(), "agent_data", "profile_snapshots.json")
                                if os.path.exists(snapshot_path):
                                    with open(snapshot_path, "r", encoding="utf-8") as f:
                                        snapshots = json.load(f) or {}

                                    p_data = snapshots.get(str(self.tag), {})
                                    p_data["followers_count"] = followers_count
                                    if isinstance(legacy.get("friends_count"), int):
                                        p_data["following_count"] = legacy.get("friends_count")
                                    p_data["x_username"] = screen_name
                                    snapshots[str(self.tag)] = p_data

                                    with open(snapshot_path, "w", encoding="utf-8") as f:
                                        json.dump(snapshots, f, ensure_ascii=False, indent=2)
                            except Exception:
                                pass
        except Exception:
            pass

    async def navigate_to_keyword_search(self, page: Any, keyword: str) -> None:
        clean_keyword = keyword.replace("(", "").replace(")", "").replace("（", "").replace("）", "").strip()
        self._print(f"🔍 准备搜索关键词: '{clean_keyword}'")

        kw_encoded = quote(clean_keyword)
        target_search_url = f"https://x.com/search?q={kw_encoded}&f=live"

        try:
            await page.goto(target_search_url, wait_until="commit", timeout=15000)
            await asyncio.sleep(random.uniform(3.5, 6.0))
            self._print("✅ 检索页面跳转成功，已切入最新推文流！")
        except Exception as e:
            self._print(f"⚠️ 页面跳转过程捕获到异常: {e}")

    async def _browse_home_feed(self, page: Any) -> None:
        self._print("🎲 [拟人消痕] 随机切回 For You 首页逛街刷帖...")
        try:
            await page.goto("https://x.com/home", wait_until="commit", timeout=12000)
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
                                await asyncio.sleep(random.uniform(2.0, 4.0))

        except Exception as e:
            self._print(f"⚠️ 逛推荐页时产生非致命异常: {e}")

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

    async def _like_multiple_posts_for_account(self, page: Any, current_article: Any, target_count: int) -> int:
        liked_count = 0
        try:
            first_like_btn = await current_article.query_selector('button[data-testid="like"]')
            if first_like_btn and await safe_human_click(page, first_like_btn, self.personality):
                liked_count += 1
                self._print(f"  └─ 👍 组合拳：成功点赞第 {liked_count} 条推文")
                await asyncio.sleep(random.uniform(2.5, 4.5))

            if liked_count < target_count:
                all_articles = await page.query_selector_all("article")
                for art in all_articles:
                    if liked_count >= target_count:
                        break
                    if not await is_element_fully_loaded(art):
                        continue

                    like_btn = await art.query_selector('button[data-testid="like"]')
                    if like_btn and await is_element_fully_loaded(like_btn):
                        btn_label = await like_btn.get_attribute("aria-label") or ""
                        if "Liked" not in btn_label and "已赞" not in btn_label and "いいね済" not in btn_label:
                            if await safe_human_click(page, like_btn, self.personality):
                                liked_count += 1
                                self._print(f"  └─ 👍 组合拳：深度下翻点赞第 {liked_count} 条推文")
                                await asyncio.sleep(random.uniform(3.0, 5.0))
        except Exception:
            pass
        return liked_count

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
            target_likes = random.randint(1, 2)
            for art in articles[:4]:
                if likes >= target_likes:
                    break
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
                await page.go_back(wait_until="commit", timeout=8000)
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

    async def _run_single_batch(self, page: Any, config: AutomationConfig, current_total_exec: int) -> tuple[int, int, int, int]:
        """运行单批次推文扫描与交互（最多单批处理 3~5 个账号）"""
        batch_limit = random.randint(3, 5)  # 单批次最多处理 3~5 个目标
        exec_count = 0
        likes_count = 0
        follows_count = 0
        views_count = 0
        consecutive_follows = 0
        empty_rounds = 0

        processed_handles = set()
        seen_articles = set()

        for round_idx in range(25):
            await self._assert_no_challenge(page)

            if exec_count >= batch_limit or (current_total_exec + exec_count) >= config.daily_task_limit:
                self._print(f"🎉 当前批次目标 ({exec_count}) 已完成，准备进入批次休息倒计时...")
                break

            # 随机切去推荐页闲逛
            if exec_count > 0 and random.random() < config.home_browse_ratio:
                await self._browse_home_feed(page)
                await self.navigate_to_keyword_search(page, config.keyword)
                await asyncio.sleep(3.0)
                continue

            # 连续关注 2 个触发 3~5 分钟强行挂机
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
            target_found_in_round = False

            for article in articles:
                art_id = id(article)
                if art_id in seen_articles:
                    continue
                seen_articles.add(art_id)

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

                            if statuses_count > config.max_statuses_threshold:
                                self._print(f"⏩ 跳过博主 @{handle} (发帖量 {statuses_count} > {config.max_statuses_threshold})")
                                await self._dismiss_hover_card(page)
                                break

                            is_match = (0 <= follower_count <= config.max_follower_threshold)

                            if is_match:
                                self._print(f"🎯 命中目标 @{handle} (粉丝数: {follower_count} <= {config.max_follower_threshold})")
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

                                        target_likes_num = random.randint(1, 3)
                                        self._print(f"🔥 [高回关率组合拳] 关注 @{handle} 成功，触发连续点赞 {target_likes_num} 条推文...")

                                        actual_likes = await self._like_multiple_posts_for_account(page, article, target_likes_num)
                                        likes_count += actual_likes
                                    else:
                                        self._print(f"⚠️ 按钮未就绪，跳过 @{handle}")
                                        await self._dismiss_hover_card(page)

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
                    self._print("⚠️ 连续 6 轮未发现新推文，刷新页面...")
                    try:
                        await page.reload(wait_until="commit", timeout=12000)
                        await asyncio.sleep(4.0)
                        seen_articles.clear()
                    except Exception:
                        pass
                    empty_rounds = 0

        return exec_count, likes_count, follows_count, views_count

    async def run(self, custom_config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = AutomationConfig.from_mapping(custom_config)
        self.tag = config.account_tag

        jitter_delay = random.uniform(2.0, 4.0)
        self._print(f"⏳ 注入拟人启动延迟: {jitter_delay:.2f} 秒...")
        await asyncio.sleep(jitter_delay)

        self._log("started", keyword=config.keyword)
        self._print("==========================================")
        self._print(f"0.21.8 倒计时循环防封版引擎启动! [CDP端口: {self.personality.port} | 性格: {self.personality.p_type}]")
        self._print(f"目标关键词: '{config.keyword}' | 单日上限: {config.daily_task_limit} | 批次间隔: {config.batch_interval_minutes} 分钟")
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
        context = None
        page = None
        resp_listener = None

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

                valid_pages = [p for p in context.pages if not p.url.startswith("devtools")]
                page = valid_pages[0] if valid_pages else await context.new_page()

                page.on("response", self._response_callback)
                resp_listener = lambda res: asyncio.create_task(self._handle_response_interception(res))
                page.on("response", resp_listener)

                if config.keyword:
                    await self.navigate_to_keyword_search(page, config.keyword)
                else:
                    try:
                        await page.goto(config.target_url, wait_until="commit", timeout=12000)
                    except Exception:
                        pass

                await self._assert_no_challenge(page)
                page_ready = await self._wait_for_page_ready(page, timeout_sec=10.0)
                if not page_ready:
                    self._print("🛑 网页加载卡顿/未正常渲染，准备平滑重试...")
                    return {"status": "PAGE_NOT_READY", "processed_count": 0}

                # ------------------- 批次倒计时主循环 -------------------
                batch_index = 1
                while total_exec < config.daily_task_limit:
                    self._print(f"\n🚀 开始执行第 {batch_index} 批次任务 (当前已累计完成: {total_exec}/{config.daily_task_limit})...")
                    
                    e_cnt, l_cnt, f_cnt, v_cnt = await self._run_single_batch(page, config, total_exec)
                    total_exec += e_cnt
                    total_likes += l_cnt
                    total_follows += f_cnt
                    total_views += v_cnt

                    if total_exec >= config.daily_task_limit:
                        self._print(f"🎉 已达到单日任务上限 ({total_exec}/{config.daily_task_limit})，全天自动化完美收官！")
                        break

                    # 批次切回首页消痕
                    self._print("🏠 批次完成：切回 For You 首页休息消痕...")
                    try:
                        await page.goto("https://x.com/home", wait_until="commit", timeout=8000)
                    except Exception:
                        pass

                    # 【倒计时打印】按分钟倒计时，并在日志中明确显示
                    interval_min = config.batch_interval_minutes
                    self._print(f"⏳ 批次结束：开启【{interval_min} 分钟】下一调度周期倒计时...")
                    
                    for remain_m in range(interval_min, 0, -1):
                        self._print(f"⏱️ [批次倒计时] 距离第 {batch_index + 1} 批次启动还剩 {remain_m} 分钟...")
                        
                        # 挂机期间模拟微小晃动
                        for _ in range(6):
                            await asyncio.sleep(10)
                            if random.random() < 0.20:
                                try:
                                    await page.mouse.wheel(0, random.choice([-40, 40]))
                                except Exception:
                                    pass

                    batch_index += 1
                    # 倒计时结束，重新切回搜索流
                    if config.keyword:
                        await self.navigate_to_keyword_search(page, config.keyword)

                return {
                    "status": "SUCCESS",
                    "processed_count": total_exec,
                    "likes": total_likes,
                    "follows": total_follows,
                    "views": total_views,
                    "url": "https://x.com/home",
                }

        except CaptchaChallengeDetected as challenge_err:
            return {
                "status": "CHALLENGE_REQUIRED",
                "processed_count": total_exec,
                "error": str(challenge_err),
                "url": challenge_err.url,
            }
        except PlaywrightError as pw_err:
            self._print(f"🚨 CDP 通信异常/浏览器已断开: {pw_err}")
            return {
                "status": "CDP_DISCONNECTED",
                "processed_count": total_exec,
                "error": str(pw_err),
            }
        finally:
            try:
                if page and resp_listener:
                    page.remove_listener("response", resp_listener)
                if browser:
                    await browser.close()
            except Exception:
                pass

    def _response_callback(self, response: Any) -> None:
        status = getattr(response, "status", 0)
        url = str(getattr(response, "url", ""))
        if status in (429, 403) or "account/access" in url:
            self._print(f"🚨 防风控警告: 检测到 HTTP {status} 或验证拦截! 目标: {url[:80]}")

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