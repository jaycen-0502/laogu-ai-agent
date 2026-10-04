# -*- coding: utf-8 -*-
"""Unit tests for target followers mode and anti-risk hardening."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.blacklist_filter import check_user_blacklist, normalize_text
from agent.x_automation_engine import (
    AutomationConfig,
    AutomationStopped,
    CaptchaChallengeDetected,
    NotLoggedInError,
    RateLimitPause,
    XAutomationEngine,
)


def test_target_creator_rotation():
    async def _run():
        engine = XAutomationEngine(cdp_url="ws://127.0.0.1:9222/dummy")
        assert engine._target_creator_idx == 0
        assert engine._creator_scanned_counts == {}

        config = AutomationConfig(
            target_creators=["elonmusk", "sama", "BillGates"],
            max_followers_per_target=2,
        )

        # Creator 1 already reached quota (2)
        engine._creator_scanned_counts["elonmusk"] = 2
        # Creator 2 reaches quota (2)
        engine._creator_scanned_counts["sama"] = 2

        mock_page = MagicMock()
        mock_page.inner_text = AsyncMock(return_value="")
        mock_page.wait_for_selector = AsyncMock()
        mock_page.query_selector_all = AsyncMock(return_value=[])

        with patch.object(engine, "_safe_goto", new=AsyncMock(return_value=True)):
            with patch.object(engine, "_scroll_with_control", new=AsyncMock()):
                with patch("asyncio.sleep", new=AsyncMock()):
                    # elonmusk (idx 0) and sama (idx 1) both reached quota, so it advances to BillGates (idx 2)
                    await engine._run_target_followers_batch(
                        mock_page, config, current_total_exec=0
                    )

        assert engine._target_creator_idx == 0  # after all 3 exhaust/complete, wraps around cleanly

    asyncio.run(_run())


def test_cloud_claim_target_invoked_in_followers_batch():
    async def _run():
        engine = XAutomationEngine(cdp_url="ws://127.0.0.1:9222/dummy")
        config = AutomationConfig(
            target_creators=["test_target"],
            max_followers_per_target=10,
            cloud_dedup_enabled=True,
        )

        # Mock candidate cell in page
        mock_cell = MagicMock()
        mock_cell.evaluate = AsyncMock(return_value=True)
        mock_cell.inner_text = AsyncMock(return_value="Candidate Name\n@cand1\nSafe bio")

        mock_link = MagicMock()
        mock_link.get_attribute = AsyncMock(return_value="/cand1")
        mock_cell.query_selector = AsyncMock(side_effect=lambda sel: mock_link if "role" in sel else None)

        mock_page = MagicMock()
        mock_page.inner_text = AsyncMock(return_value="")
        mock_page.wait_for_selector = AsyncMock()
        mock_page.query_selector_all = AsyncMock(side_effect=[[mock_cell], []])
        mock_page.evaluate = AsyncMock(return_value=1000)

        # Mock _cloud_claim_target to deny claim (another account claimed it)
        mock_claim = AsyncMock(return_value=(False, "CLAIMED_BY_ANOTHER_ACCOUNT"))
        engine._cloud_claim_target = mock_claim

        mock_interact = AsyncMock(return_value=(True, 1))
        engine._interact_on_profile_page_simple = mock_interact

        with patch.object(engine, "_safe_goto", new=AsyncMock(return_value=True)):
            with patch.object(engine, "_safe_go_back", new=AsyncMock(return_value=True)):
                with patch.object(engine, "_scroll_with_control", new=AsyncMock()):
                    with patch("asyncio.sleep", new=AsyncMock()):
                        exec_count, _, _, _ = await engine._run_target_followers_batch(
                            mock_page, config, current_total_exec=0
                        )

        assert mock_claim.called
        assert mock_interact.call_count == 0
        assert exec_count == 0

    asyncio.run(_run())


def test_control_exceptions_re_raised_in_profile_interaction():
    async def _run():
        engine = XAutomationEngine(cdp_url="ws://127.0.0.1:9222/dummy")

        mock_page = MagicMock()
        mock_page.wait_for_selector = AsyncMock()
        mock_page.scroll_with_control = AsyncMock()
        mock_page.inner_text = AsyncMock(return_value="")

        with patch.object(engine, "_scroll_with_control", side_effect=AutomationStopped("User stopped task")):
            with pytest.raises(AutomationStopped):
                await engine._interact_on_profile_page_simple(mock_page, "cand_user")

        with patch.object(engine, "_scroll_with_control", side_effect=NotLoggedInError("Not logged in")):
            with pytest.raises(NotLoggedInError):
                await engine._interact_on_profile_page_simple(mock_page, "cand_user")

        with patch.object(engine, "_scroll_with_control", side_effect=CaptchaChallengeDetected("Arkose CAPTCHA")):
            with pytest.raises(CaptchaChallengeDetected):
                await engine._interact_on_profile_page_simple(mock_page, "cand_user")

    asyncio.run(_run())


def test_nfkc_unicode_normalization_veto():
    user = {
        "display_name": "副業【\uff21\uff29】マスター",
        "screen_name": "ai_master",
        "bio": "初心者でも月100万！",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user, ["AI"])
    assert hit is True
    assert word == "AI"

    user2 = {
        "display_name": "一般人",
        "screen_name": "normal_guy",
        "bio": "趣味は\uff26\uff38と読书",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user2, ["fx"])
    assert hit is True
