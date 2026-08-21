"""Safe, read-only Playwright CDP workflow validation engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import random
import re
from typing import Any


class AutomationEngineError(RuntimeError):
    pass


class RateLimitPause(AutomationEngineError):
    def __init__(self, seconds: int):
        super().__init__(f"Rate limit detected; pause for {seconds} seconds")
        self.seconds = seconds


@dataclass(frozen=True)
class AutomationConfig:
    keyword: str = ""
    daily_task_limit: int = 50
    max_follower_threshold: int = 150
    max_engagement_threshold: int = 10000
    target_url: str = "https://x.com/home"
    navigation_timeout_ms: int = 30_000
    sleep_on_rate_limit: bool = True
    post_batch_cooldown_seconds: int = 0
    daily_tasks_used: int = 0

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "AutomationConfig":
        values = values or {}
        if isinstance(values.get("active"), dict):
            merged = dict(values["active"])
            merged.update({k: v for k, v in values.items() if k != "active"})
            values = merged

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(values.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        return cls(
            keyword=str(values.get("keyword") or values.get("keywords") or "").strip()[:200],
            daily_task_limit=integer("daily_task_limit", 50, 1, 10_000),
            max_follower_threshold=integer("max_follower_threshold", 150, 0, 100_000_000),
            max_engagement_threshold=integer("max_engagement_threshold", 10_000, 0, 100_000_000),
            target_url=str(values.get("target_url") or cls.target_url).strip()[:500],
            navigation_timeout_ms=integer("navigation_timeout_ms", 30_000, 5_000, 120_000),
            sleep_on_rate_limit=bool(values.get("sleep_on_rate_limit", True)),
            post_batch_cooldown_seconds=integer("post_batch_cooldown_seconds", 0, 0, 300),
            daily_tasks_used=integer("daily_tasks_used", 0, 0, 10_000),
        )


class XAutomationEngine:
    """Attach through CDP for navigation, snapshot filtering, and status checks only."""

    RATE_LIMIT_PATTERNS = (
        re.compile(r"rate limit", re.I),
        re.compile(r"too many requests", re.I),
        re.compile(r"请求过于频繁"),
        re.compile(r"操作频率过高"),
        re.compile(r"速率限制"),
    )

    def __init__(self, *, cdp_url: str, logger: logging.Logger | None = None):
        if not str(cdp_url).strip():
            raise ValueError("cdp_url is required")
        self.cdp_url = str(cdp_url).strip()
        self.logger = logger or logging.getLogger("laogu-ai-agent.x-automation")

    async def run(self, custom_config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = AutomationConfig.from_mapping(custom_config)
        if config.daily_tasks_used >= config.daily_task_limit:
            return {"status": "SKIPPED", "read_only": True, "reason": "DAILY_TASK_LIMIT_REACHED", "daily_tasks_used": config.daily_tasks_used, "daily_task_limit": config.daily_task_limit}
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AutomationEngineError("Playwright is not installed") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            target_url = config.target_url
            if config.keyword:
                target_url = f"https://x.com/search?q={re.sub(r'\s+', '%20', config.keyword)}&f=live"
            await page.goto(target_url, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)
            body_text = await page.locator("body").inner_text(timeout=config.navigation_timeout_ms)
            marker = self._find_rate_limit(body_text)
            if marker:
                seconds = random.randint(23 * 60, 40 * 60)
                if config.sleep_on_rate_limit:
                    await asyncio.sleep(seconds)
                raise RateLimitPause(seconds)
            result = self._filter_read_only_snapshot(body_text, url=page.url, title=await page.title(), config=config)
            if config.post_batch_cooldown_seconds:
                await asyncio.sleep(config.post_batch_cooldown_seconds)
            return result

    @classmethod
    def _find_rate_limit(cls, text: str) -> str | None:
        for pattern in cls.RATE_LIMIT_PATTERNS:
            match = pattern.search(text or "")
            if match:
                return match.group(0)
        return None

    @staticmethod
    def _filter_read_only_snapshot(text: str, *, url: str, title: str, config: AutomationConfig) -> dict[str, Any]:
        normalized = text.casefold()
        matched = not config.keyword or config.keyword.casefold() in normalized
        numbers = [int(value.replace(",", "")) for value in re.findall(r"\b\d{1,3}(?:,\d{3})*\b", text)]
        follower_value = numbers[0] if numbers else None
        engagement_value = numbers[1] if len(numbers) > 1 else None
        eligible = matched
        if follower_value is not None:
            eligible = eligible and follower_value <= config.max_follower_threshold
        if engagement_value is not None:
            eligible = eligible and engagement_value <= config.max_engagement_threshold
        return {"status": "SUCCESS", "read_only": True, "matched": matched, "eligible": eligible, "keyword": config.keyword, "follower_value": follower_value, "engagement_value": engagement_value, "daily_task_limit": config.daily_task_limit, "url": url, "title": title}

