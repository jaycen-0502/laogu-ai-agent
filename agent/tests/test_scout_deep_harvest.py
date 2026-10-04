import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agent.x_automation_engine import AutomationConfig, XAutomationEngine


def test_automation_config_scout_deep_harvest_defaults():
    cfg = AutomationConfig()
    assert cfg.max_harvest_per_seed == 10
    assert cfg.in_situ_rest_enabled is True
    assert cfg.graphql_scout_filter_enabled is True
    assert cfg.smart_newbie_recognition_enabled is True
    assert cfg.chain_hop_enabled is False
    assert cfg.chain_hop_source == "followers"

    # Test mapping parsing
    custom = AutomationConfig.from_mapping({
        "max_harvest_per_seed": "12",
        "in_situ_rest_enabled": "false",
        "graphql_scout_filter_enabled": "false",
        "smart_newbie_recognition_enabled": "false",
        "chain_hop_enabled": "false",
        "chain_hop_source": "following",
    })
    assert custom.max_harvest_per_seed == 12
    assert custom.in_situ_rest_enabled is False
    assert custom.graphql_scout_filter_enabled is False
    assert custom.smart_newbie_recognition_enabled is False
    assert custom.chain_hop_enabled is False
    assert custom.chain_hop_source == "following"

    # Invalid value fallback
    fallback = AutomationConfig.from_mapping({
        "chain_hop_source": "invalid_mode",
    })
    assert fallback.chain_hop_source == "followers"


def test_engine_init_in_situ_state_variables():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    assert engine._in_situ_list_active is False
    assert engine._in_situ_seed_handle == ""
    assert engine._seed_harvest_counts == {}


@pytest.mark.anyio
async def test_chain_hop_gate_conditions():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    page = AsyncMock()

    # Scenario 1: Chain hop is disabled (chain_hop_enabled=False)
    # Must immediately return 0, 0, 0, 0 regardless of scout mode
    cfg_no_hop = AutomationConfig.from_mapping({
        "chain_hop_enabled": False,
        "graphql_scout_filter_enabled": True,
    })
    with patch.object(engine, "_assert_no_challenge", AsyncMock()):
        res = await engine._chain_hop_following_exploration(
            page, "target_user", cfg_no_hop, 1, 0, 5
        )
        assert res == (0, 0, 0, 0)

    # Scenario 2: Scout mode is enabled AND chain hop is enabled
    # Must delegate to _scout_deep_harvest_following_exploration
    cfg_scout_and_hop = AutomationConfig.from_mapping({
        "chain_hop_enabled": True,
        "graphql_scout_filter_enabled": True,
        "max_harvest_per_seed": 10,
    })
    with patch.object(engine, "_assert_no_challenge", AsyncMock()):
        with patch.object(
            engine,
            "_scout_deep_harvest_following_exploration",
            AsyncMock(return_value=(3, 3, 3, 10))
        ) as mock_deep:
            res = await engine._chain_hop_following_exploration(
                page, "target_user", cfg_scout_and_hop, 1, 0, 5
            )
            assert res == (3, 3, 3, 10)
            mock_deep.assert_awaited_once_with(
                page=page,
                start_handle="target_user",
                config=cfg_scout_and_hop,
                current_chain_depth=1,
                current_total_exec=0,
                remaining_batch_budget=5,
            )


@pytest.mark.anyio
async def test_scout_deep_harvest_saturation_cap():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    page = AsyncMock()
    page.url = "https://x.com/seed_user/following"

    cfg = AutomationConfig.from_mapping({
        "chain_hop_enabled": True,
        "graphql_scout_filter_enabled": True,
        "max_harvest_per_seed": 5,
    })

    # Pre-populate seed harvest count to saturation
    engine._seed_harvest_counts["seed_user"] = 5
    engine._in_situ_list_active = True

    with patch.object(engine, "_assert_no_challenge", AsyncMock()), \
         patch.object(engine, "_safe_go_back", AsyncMock(return_value=True)) as mock_back:
        res = await engine._scout_deep_harvest_following_exploration(
            page, "seed_user", cfg, 1, 0, 3
        )
        # Should return 0 actions and clear in-situ flag
        assert res == (0, 0, 0, 0)
        assert engine._in_situ_list_active is False
        mock_back.assert_awaited_once()


@pytest.mark.anyio
async def test_scout_deep_harvest_in_situ_rest_trigger():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    page = AsyncMock()
    page.url = "https://x.com/seed_user/following"

    cfg = AutomationConfig.from_mapping({
        "chain_hop_enabled": True,
        "graphql_scout_filter_enabled": True,
        "max_harvest_per_seed": 10,
        "in_situ_rest_enabled": True,
    })

    # Mock user cells
    cell1 = AsyncMock()
    cell1.evaluate = AsyncMock(return_value=True)
    cell1.inner_text = AsyncMock(return_value="Friend One")
    cell1.query_selector = AsyncMock(side_effect=lambda sel: AsyncMock() if "UserCell" in sel or "role" in sel else None)

    cand_handle = "candidate_friend_999"
    link1 = AsyncMock()
    link1.get_attribute = AsyncMock(return_value=f"/{cand_handle}")
    cell1.query_selector = AsyncMock(side_effect=lambda sel: link1 if "role" in sel else None)

    page.query_selector_all = AsyncMock(return_value=[cell1])

    # Interacting with candidate succeeds
    with patch.object(engine, "_assert_no_challenge", AsyncMock()), \
         patch.object(engine.history_pool, "is_visited", MagicMock(return_value=False)), \
         patch.object(engine, "user_cache", {cand_handle: {"followers_count": 50, "verified": False}}), \
         patch.object(engine, "_cloud_claim_target", AsyncMock(return_value=(True, "OK"))), \
         patch.object(engine, "_interact_on_profile_page_simple", AsyncMock(return_value=(True, 1))), \
         patch.object(engine, "_cloud_confirm_target", AsyncMock()), \
         patch.object(engine, "_safe_go_back", AsyncMock(return_value=True)), \
         patch.object(engine, "_sleep_with_natural_dwell", AsyncMock()), \
         patch.object(engine, "_execute_follow_burst_protection", AsyncMock(return_value=0)):

        # Remaining budget is 1, so after following candidate, batch budget is met!
        res = await engine._scout_deep_harvest_following_exploration(
            page, "seed_user", cfg, 1, 0, 1
        )
        assert res[0] == 1  # 1 exec
        assert res[2] == 1  # 1 follow
        assert engine._seed_harvest_counts["seed_user"] == 1
        # Batch budget is reached (1 >= 1) and total_seed_done (1) < harvest_cap (10)
        # Therefore Strategy A in-situ rest MUST BE TRIGGERED:
        assert engine._in_situ_list_active is True
        assert engine._in_situ_seed_handle == "seed_user"


@pytest.mark.anyio
async def test_scout_deep_harvest_prefilter_locked_and_differential_micro_rest():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    page = AsyncMock()
    page.url = "https://x.com/seed_user/following"

    cfg = AutomationConfig.from_mapping({
        "chain_hop_enabled": True,
        "graphql_scout_filter_enabled": True,
        "max_harvest_per_seed": 10,
        "in_situ_rest_enabled": True,
    })

    # Cell 1: Locked account (protected=True in user_cache) -> must be skipped without visit
    cell_locked = AsyncMock()
    cell_locked.evaluate = AsyncMock(return_value=True)
    link_locked = AsyncMock()
    link_locked.get_attribute = AsyncMock(return_value="/locked_user_111")
    cell_locked.query_selector = AsyncMock(side_effect=lambda sel: link_locked if "role" in sel else None)

    # Cell 2: Normal valid candidate
    cell_valid = AsyncMock()
    cell_valid.evaluate = AsyncMock(return_value=True)
    link_valid = AsyncMock()
    link_valid.get_attribute = AsyncMock(return_value="/valid_user_222")
    cell_valid.query_selector = AsyncMock(side_effect=lambda sel: link_valid if "role" in sel else None)

    page.query_selector_all = AsyncMock(return_value=[cell_locked, cell_valid])

    engine.user_cache["locked_user_111"] = {"followers_count": 50, "protected": True}
    engine.user_cache["valid_user_222"] = {"followers_count": 50, "protected": False}
    engine._scout_candidates["valid_user_222"] = {"followers_count": 50, "screen_name": "valid_user_222"}

    mock_dwell = AsyncMock()
    mock_interact = AsyncMock(return_value=(True, 1))

    with patch.object(engine, "_assert_no_challenge", AsyncMock()), \
         patch.object(engine.history_pool, "is_visited", MagicMock(return_value=False)), \
         patch.object(engine, "_cloud_claim_target", AsyncMock(return_value=(True, "OK"))), \
         patch.object(engine, "_interact_on_profile_page_simple", mock_interact), \
         patch.object(engine, "_cloud_confirm_target", AsyncMock()), \
         patch.object(engine, "_safe_go_back", AsyncMock(return_value=True)), \
         patch.object(engine, "_sleep_with_natural_dwell", mock_dwell), \
         patch.object(engine, "_execute_follow_burst_protection", AsyncMock(return_value=0)):

        res = await engine._scout_deep_harvest_following_exploration(
            page, "seed_user", cfg, 1, 0, 1
        )

        # locked_user_111 was pre-filtered in memory, so _interact was ONLY called for valid_user_222!
        assert mock_interact.call_count == 1
        args, kwargs = mock_interact.call_args
        assert args[1] == "valid_user_222"

        # Since newly_followed was True for valid_user_222, dwell was called for valid_user_222 (if budget not reached before dwell check)
        assert res[0] == 1
        assert res[2] == 1


@pytest.mark.anyio
async def test_ensure_correct_followers_tab_languages():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    page = AsyncMock()

    # Case 1: Chinese tabs - Tab 0 is 认证关注者 (should be skipped), Tab 1 is 关注者 (should be clicked)
    tab_verified = AsyncMock()
    tab_verified.get_attribute = AsyncMock(side_effect=lambda attr: "/testuser/verified_followers" if attr == "href" else "false")
    tab_verified.inner_text = AsyncMock(return_value="认证关注者")

    tab_followers = AsyncMock()
    tab_followers.get_attribute = AsyncMock(side_effect=lambda attr: "/testuser/followers" if attr == "href" else "false")
    tab_followers.inner_text = AsyncMock(return_value="关注者")

    page.url = "https://x.com/testuser/verified_followers"
    page.query_selector_all = AsyncMock(return_value=[tab_verified, tab_followers])

    with patch("agent.x_automation_engine.safe_human_click", AsyncMock()) as mock_click:
        res = await engine._ensure_correct_followers_tab(page)
        assert res is True
        mock_click.assert_awaited_once_with(page, tab_followers, engine.personality)

    # Case 2: Japanese tabs - Tab 0 is 認証済みフォロワー, Tab 1 is フォロワー
    tab_ja_verified = AsyncMock()
    tab_ja_verified.get_attribute = AsyncMock(side_effect=lambda attr: "/testuser/verified_followers" if attr == "href" else "false")
    tab_ja_verified.inner_text = AsyncMock(return_value="認証済みフォロワー")

    tab_ja_followers = AsyncMock()
    tab_ja_followers.get_attribute = AsyncMock(side_effect=lambda attr: "/testuser/followers" if attr == "href" else "false")
    tab_ja_followers.inner_text = AsyncMock(return_value="フォロワー")

    page.query_selector_all = AsyncMock(return_value=[tab_ja_verified, tab_ja_followers])

    with patch("agent.x_automation_engine.safe_human_click", AsyncMock()) as mock_click_ja:
        res_ja = await engine._ensure_correct_followers_tab(page)
        assert res_ja is True
        mock_click_ja.assert_awaited_once_with(page, tab_ja_followers, engine.personality)


@pytest.mark.anyio
async def test_ensure_correct_following_tab_languages():
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")
    page = AsyncMock()

    # Japanese Following tab: フォロー中
    tab_following_ja = AsyncMock()
    tab_following_ja.get_attribute = AsyncMock(side_effect=lambda attr: "/testuser/following" if attr == "href" else "false")
    tab_following_ja.inner_text = AsyncMock(return_value="フォロー中")

    page.url = "https://x.com/testuser/followers"
    page.query_selector_all = AsyncMock(return_value=[tab_following_ja])

    with patch("agent.x_automation_engine.safe_human_click", AsyncMock()) as mock_click:
        res = await engine._ensure_correct_following_tab(page)
        assert res is True
        mock_click.assert_awaited_once_with(page, tab_following_ja, engine.personality)

