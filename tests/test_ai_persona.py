# -*- coding: utf-8 -*-
"""
本地 AI 账号人设与模型绑定功能测试
验证：
1. 本地人设配置读取与持久化
2. 账号级独立设定与继承全局默认
3. 自由编写人设与自定义模板保存/选用
4. AutomationConfig 解析 ai_persona 与 dm_password
5. generate_ai_reply 注入人设提示词与温度
6. _handle_dm_password_prompt_if_present 模拟打字解锁私信密码弹窗
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from agent.x_automation_engine import AutomationConfig, generate_ai_reply, XAutomationEngine
from desktop.ai_persona_dialog import AIPersonaConfigDialog, BUILTIN_PERSONA_TEMPLATES
from desktop.controller import DesktopController, AccountRow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_controller_persona_config_lifecycle(tmp_path):
    settings_mock = MagicMock()
    settings_mock.log_file = tmp_path / "logs" / "desktop.log"
    settings_mock.server_url = "https://mock.test"

    controller = DesktopController.__new__(DesktopController)
    controller.settings = settings_mock
    controller.logger = MagicMock()

    # 1. 默认读取
    initial_cfg = controller.get_account_personas_config()
    assert "default_persona" in initial_cfg
    assert "personas" in initial_cfg
    assert initial_cfg["default_persona"]["lock_persona"] is True

    # 2. 修改并保存
    test_cfg = {
        "version": 1,
        "default_persona": {
            "persona_name": "测试女性人设",
            "system_prompt": "你是一名测试角色",
            "custom_model": "",
            "temperature": 0.8,
            "lock_persona": True,
            "dm_password": "my_secret_pin_123",
        },
        "personas": {
            "prof_1001": {
                "persona_name": "专属御姐",
                "system_prompt": "高冷知性",
                "custom_model": "",
                "temperature": 0.65,
                "lock_persona": True,
                "dm_password": "account_pwd_999",
            }
        },
    }

    ok = controller.save_account_personas_config(test_cfg)
    assert ok is True

    # 3. 验证账号专属与继承逻辑
    custom_persona = controller.get_persona_for_account("prof_1001")
    assert custom_persona["persona_name"] == "专属御姐"
    assert custom_persona["dm_password"] == "account_pwd_999"
    assert custom_persona["temperature"] == 0.65

    # 未配置的账号应继承全局默认
    inherited_persona = controller.get_persona_for_account("prof_9999")
    assert inherited_persona["persona_name"] == "测试女性人设"
    assert inherited_persona["dm_password"] == "my_secret_pin_123"
    assert inherited_persona["temperature"] == 0.8


def test_ai_persona_dialog_custom_template_and_dm_password(qapp):
    controller = MagicMock()
    controller.get_account_personas_config.return_value = {
        "version": 1,
        "default_persona": {
            "persona_name": "默认温柔",
            "system_prompt": "默认提示词内容",
            "custom_model": "",
            "temperature": 0.75,
            "lock_persona": True,
            "dm_password": "init_pwd",
        },
        "personas": {},
        "custom_templates": {
            "自定义甜美少女": {
                "persona_name": "甜美少女",
                "system_prompt": "超级甜美的口吻",
                "temperature": 0.88,
                "lock_persona": True,
            }
        },
    }
    controller.save_account_personas_config.return_value = True

    mock_account = AccountRow(
        profile_id="prof_001",
        profile_name="矩阵1号小号",
        instance_id="inst_1",
        browser_status="STOPPED",
        login_status="LOGGED_IN",
        x_username="alice_tokyo",
        x_account_id="12345",
        account_status="ACTIVE",
        last_checked="",
    )

    dialog = AIPersonaConfigDialog(controller, accounts=[mock_account])

    # 1. 验证初始加载，不应包含模型下拉框
    assert not hasattr(dialog, "model_combo")
    assert dialog.persona_name_edit.text() == "默认温柔"
    assert dialog.dm_password_edit.text() == "init_pwd"
    assert dialog.account_combo.count() == 2

    # 2. 切换至指定账号
    dialog.account_combo.setCurrentIndex(1)
    assert dialog._current_profile_id == "prof_001"

    # 3. 选用自定义模板
    dialog._apply_custom_template("自定义甜美少女")
    assert dialog.persona_name_edit.text() == "甜美少女"
    assert "超级甜美" in dialog.prompt_edit.toPlainText()

    # 4. 自由编写修改
    dialog.persona_name_edit.setText("我的专属定制")
    dialog.prompt_edit.setPlainText("自由输入的独家口吻")
    dialog.dm_password_edit.setText("new_pin_888")

    # 5. 点击保存
    dialog._on_save_clicked()
    assert controller.save_account_personas_config.called
    saved_payload = controller.save_account_personas_config.call_args[0][0]
    assert "prof_001" in saved_payload["personas"]
    prof_data = saved_payload["personas"]["prof_001"]
    assert prof_data["persona_name"] == "我的专属定制"
    assert prof_data["system_prompt"] == "自由输入的独家口吻"
    assert prof_data["dm_password"] == "new_pin_888"
    assert prof_data["custom_model"] == ""
    dialog.close()


def test_automation_config_parses_dm_password():
    cfg = AutomationConfig.from_mapping({
        "keyword": "Tokyo",
        "dm_password": "super_secret_pin_777",
        "ai_persona": {
            "persona_name": "知性御姐",
            "system_prompt": "专属设定提示词",
        }
    })
    assert cfg.dm_password == "super_secret_pin_777"
    assert cfg.ai_persona["persona_name"] == "知性御姐"


@pytest.mark.anyio
async def test_handle_dm_password_prompt_simulation():
    engine = XAutomationEngine.__new__(XAutomationEngine)
    engine._active_config = AutomationConfig(dm_password="test_unlock_password_456")
    engine.personality = MagicMock()
    engine.logger = MagicMock()
    engine._print = MagicMock()

    mock_input = AsyncMock()
    mock_input.is_visible.return_value = True

    mock_confirm_btn = AsyncMock()
    mock_confirm_btn.is_visible.return_value = True

    async def mock_query_selector(selector):
        if "input" in selector:
            return mock_input
        if "button" in selector:
            return mock_confirm_btn
        return None

    mock_page = MagicMock()
    mock_page.query_selector = mock_query_selector
    mock_page.keyboard = AsyncMock()

    with patch("agent.x_automation_engine.safe_human_click", new_callable=AsyncMock) as mock_click, \
         patch("agent.x_automation_engine.human_type_text", new_callable=AsyncMock) as mock_type, \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_click.return_value = True
        handled = await engine._handle_dm_password_prompt_if_present(mock_page)

        assert handled is True
        assert mock_click.called
        assert mock_type.called
        # 确认调用的打字密码为配置的私信密码
        assert mock_type.call_args[0][2] == "test_unlock_password_456"
