import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent import x_automation_engine as engine_module
from agent.history_pool import PersistentHistoryPool


@pytest.fixture
def profile_context(monkeypatch, tmp_path):
    pool = PersistentHistoryPool(storage_file=tmp_path / "history.json")
    engine = engine_module.XAutomationEngine(cdp_url="http://127.0.0.1:9222", history_pool=pool)
    engine.tag = "worker-1"
    engine._print = Mock()
    engine._allow_action = AsyncMock(return_value=True)
    engine._verify_follow_success = AsyncMock(return_value=(True, ""))
    engine._record_action = Mock()
    engine.navigate_to_keyword_search = AsyncMock()
    page = SimpleNamespace(
        query_selector=AsyncMock(return_value=None),
        query_selector_all=AsyncMock(return_value=[]),
        go_back=AsyncMock(),
    )
    monkeypatch.setattr(engine_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(engine_module, "human_discrete_scroll", AsyncMock())
    monkeypatch.setattr(engine_module, "is_element_fully_loaded", AsyncMock(return_value=True))
    monkeypatch.setattr(engine_module, "safe_human_click", AsyncMock(return_value=True))
    return engine, page


@pytest.mark.parametrize("header_state", ["missing", "unloaded", "empty", "ready"])
def test_primary_action_requires_validated_header(profile_context, header_state):
    engine, page = profile_context
    sidebar_button = object()
    header_button = object()
    header = SimpleNamespace(query_selector=AsyncMock(
        return_value=None if header_state == "empty" else header_button,
    ))

    async def query(selector):
        if selector == 'div[data-testid="UserProfileHeader_Root"]':
            return None if header_state == "missing" else header
        if selector == 'button[data-testid$="-follow"]':
            return sidebar_button
        return None

    page.query_selector.side_effect = query
    engine_module.is_element_fully_loaded.return_value = header_state != "unloaded"
    result = asyncio.run(engine._interact_on_profile_page(page, "Target", "work"))

    assert result == (header_state == "ready", 0)
    assert not any('-follow' in call.args[0] for call in page.query_selector.await_args_list)
    if header_state == "ready":
        engine_module.safe_human_click.assert_awaited_once_with(page, header_button, engine.personality)
        engine._record_action.assert_called_once_with("follow", "handle:target")
    else:
        engine_module.safe_human_click.assert_not_awaited()
        engine._allow_action.assert_not_awaited()
        assert any("validated header" in call.args[0] for call in engine._print.call_args_list)
    if header_state in {"missing", "unloaded"}:
        header.query_selector.assert_not_awaited()
    page.go_back.assert_awaited_once()
    engine.navigate_to_keyword_search.assert_not_awaited()


@pytest.mark.parametrize("state", ["lock", "empty", "Content is PROTECTED", "内容受保护"])
def test_unavailable_profile_skips_content_and_persists_history(profile_context, state):
    engine, page = profile_context

    async def query(selector):
        if state == "lock" and 'svg[data-testid="icon-lock"]' in selector:
            return object()
        if state == "empty" and '[data-testid="empty_state"]' in selector:
            return object()
        if selector.startswith("text=/") and re.search(selector[6:-2], state, re.IGNORECASE):
            return object()
        return None

    page.query_selector.side_effect = query
    assert asyncio.run(engine._interact_on_profile_page(page, "Target", "work")) == (False, 0)
    engine_module.human_discrete_scroll.assert_not_awaited()
    engine_module.safe_human_click.assert_not_awaited()
    page.query_selector_all.assert_not_awaited()
    engine._allow_action.assert_not_awaited()
    engine.navigate_to_keyword_search.assert_awaited_once_with(page, "work")
    restored = PersistentHistoryPool(storage_file=engine.history_pool.file_path)
    assert restored.is_visited("worker-1", "target")
    assert not restored.is_visited("worker-2", "target")


@pytest.mark.parametrize("character", list(".,!?;:\n"))
def test_separator_adds_pause_after_character(monkeypatch, character):
    sleep = AsyncMock()
    page = SimpleNamespace(keyboard=SimpleNamespace(type=AsyncMock()))
    element = SimpleNamespace(click=AsyncMock())
    monkeypatch.setattr(engine_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(engine_module.random, "uniform", lambda lower, upper: (lower + upper) / 2)
    monkeypatch.setattr(engine_module.random, "lognormvariate", lambda *args: 0.1)

    asyncio.run(engine_module.human_type_text(page, element, character))

    page.keyboard.type.assert_awaited_once_with(character)
    assert [call.args[0] for call in sleep.await_args_list] == pytest.approx([0.9, 0.1, 0.4, 1.15])


@pytest.mark.parametrize("character, probability, extra", [
    (" ", 0.29, [0.2]), (" ", 0.3, []), (" ", 0.9, []), ("a", 0.1, []),
])
def test_space_cadence_is_occasional(monkeypatch, character, probability, extra):
    sleep = AsyncMock()
    page = SimpleNamespace(keyboard=SimpleNamespace(type=AsyncMock()))
    monkeypatch.setattr(engine_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(engine_module.random, "uniform", lambda lower, upper: (lower + upper) / 2)
    monkeypatch.setattr(engine_module.random, "lognormvariate", lambda *args: 0.1)
    monkeypatch.setattr(engine_module.random, "random", lambda: probability)

    asyncio.run(engine_module.human_type_text(page, SimpleNamespace(click=AsyncMock()), character))

    assert [call.args[0] for call in sleep.await_args_list] == pytest.approx([0.9, 0.1, *extra, 1.15])
