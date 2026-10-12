from PySide6.QtWidgets import QApplication

from desktop.main_window import TaskConfigDialog


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_advanced_interaction_limits_are_expanded_without_changing_config_values():
    _application()
    dialog = TaskConfigDialog(
        {
            "daily_task_limit": 31,
            "allow_like": False,
            "daily_likes_limit": 7,
            "daily_follows_limit": 4,
        }
    )

    assert dialog.advanced_interaction_toggle.isChecked()
    assert not dialog.advanced_interaction_content.isHidden()
    assert dialog.config()["daily_task_limit"] == 31
    assert dialog.config()["allow_like"] is False
    assert dialog.config()["daily_likes_limit"] == 7
    assert dialog.config()["daily_follows_limit"] == 4

    dialog.advanced_interaction_toggle.setChecked(False)
    assert dialog.advanced_interaction_content.isHidden()
    assert dialog.config()["daily_likes_limit"] == 7
    dialog.close()


def test_new_task_defaults_to_unlimited_advanced_interaction_limits():
    _application()
    dialog = TaskConfigDialog()

    config = dialog.config()
    assert config["daily_task_limit"] == 100
    assert all(
        config[key] == 0
        for key in (
            "daily_likes_limit",
            "daily_follows_limit",
            "daily_replies_limit",
            "daily_bookmarks_limit",
            "daily_retweets_limit",
        )
    )
    assert config["allow_like"] is True
    assert config["allow_follow"] is True
    assert config["allow_reply"] is False
    assert config["allow_bookmark"] is False
    assert config["allow_retweet"] is False
    assert config["retweet_ratio"] == 0.10
    dialog.close()


def test_retweet_strategy_loads_saves_and_disables_probability_when_not_allowed():
    _application()
    dialog = TaskConfigDialog({"allow_retweet": False, "retweet_ratio": 0.35})

    assert not dialog.allow_retweet_input.isChecked()
    assert not dialog.retweet_ratio_input.isEnabled()
    assert dialog.retweet_ratio_input.value() == 0.35
    assert dialog.config()["allow_retweet"] is False
    assert dialog.config()["retweet_ratio"] == 0.35

    dialog.allow_retweet_input.setChecked(True)
    assert dialog.retweet_ratio_input.isEnabled()
    dialog.retweet_ratio_input.setValue(0.55)
    assert dialog.config()["allow_retweet"] is True
    assert dialog.config()["retweet_ratio"] == 0.55

    # The dialog also receives account configs asynchronously when switching
    # accounts, so verify the late-load path updates both controls.
    dialog.apply_initial({"active": {"allow_retweet": False, "retweet_ratio": 0.20}})
    assert not dialog.allow_retweet_input.isChecked()
    assert not dialog.retweet_ratio_input.isEnabled()
    assert dialog.config()["retweet_ratio"] == 0.20
    dialog.close()


def test_anti_risk_switches_defaults():
    _application()
    dialog = TaskConfigDialog()

    assert not dialog.warmup_ramp_enabled_input.isChecked()
    assert not dialog.casual_tweet_enabled_input.isChecked()
    assert not dialog.casual_tweet_topic_input.isEnabled()
    assert not dialog.smart_unfollow_enabled_input.isChecked()
    assert not dialog.unfollow_threshold_days_input.isEnabled()
    assert dialog.unfollow_threshold_days_input.value() == 5

    config = dialog.config()
    assert config["warmup_ramp_enabled"] is False
    assert config["casual_tweet_enabled"] is False
    assert config["casual_tweet_topic"] == ""
    assert config["smart_unfollow_enabled"] is False
    assert config["unfollow_threshold_days"] == 5
    dialog.close()


def test_anti_risk_switches_custom_values_and_toggles():
    _application()
    dialog = TaskConfigDialog(
        {
            "warmup_ramp_enabled": True,
            "casual_tweet_enabled": True,
            "casual_tweet_topic": "#日常 #カフェ",
            "smart_unfollow_enabled": True,
            "unfollow_threshold_days": 7,
        }
    )

    assert dialog.warmup_ramp_enabled_input.isChecked()
    assert dialog.casual_tweet_enabled_input.isChecked()
    assert dialog.casual_tweet_topic_input.isEnabled()
    assert dialog.casual_tweet_topic_input.text() == "#日常 #カフェ"
    assert dialog.smart_unfollow_enabled_input.isChecked()
    assert dialog.unfollow_threshold_days_input.isEnabled()
    assert dialog.unfollow_threshold_days_input.value() == 7

    cfg = dialog.config()
    assert cfg["warmup_ramp_enabled"] is True
    assert cfg["casual_tweet_enabled"] is True
    assert cfg["casual_tweet_topic"] == "#日常 #カフェ"
    assert cfg["smart_unfollow_enabled"] is True
    assert cfg["unfollow_threshold_days"] == 7

    # Test toggles disable sub-inputs
    dialog.casual_tweet_enabled_input.setChecked(False)
    assert not dialog.casual_tweet_topic_input.isEnabled()

    dialog.smart_unfollow_enabled_input.setChecked(False)
    assert not dialog.unfollow_threshold_days_input.isEnabled()

    # Test apply_initial updates correctly
    dialog.apply_initial(
        {
            "active": {
                "warmup_ramp_enabled": False,
                "casual_tweet_enabled": True,
                "casual_tweet_topic": "#婚活",
                "smart_unfollow_enabled": True,
                "unfollow_threshold_days": 3,
            }
        }
    )
    assert not dialog.warmup_ramp_enabled_input.isChecked()
    assert dialog.casual_tweet_enabled_input.isChecked()
    assert dialog.casual_tweet_topic_input.isEnabled()
    assert dialog.casual_tweet_topic_input.text() == "#婚活"
    assert dialog.smart_unfollow_enabled_input.isChecked()
    assert dialog.unfollow_threshold_days_input.isEnabled()
    assert dialog.unfollow_threshold_days_input.value() == 3
    dialog.close()


def test_batch_jitter_and_burst_cooling_switches():
    _application()
    # 1. Default should be True for both
    dialog = TaskConfigDialog()
    assert dialog.batch_jitter_enabled_input.isChecked()
    assert dialog.follow_burst_cooling_enabled_input.isChecked()
    cfg = dialog.config()
    assert cfg["batch_jitter_enabled"] is True
    assert cfg["follow_burst_cooling_enabled"] is True
    dialog.close()

    # 2. Custom values (False)
    dialog2 = TaskConfigDialog(
        {
            "batch_jitter_enabled": False,
            "follow_burst_cooling_enabled": False,
        }
    )
    assert not dialog2.batch_jitter_enabled_input.isChecked()
    assert not dialog2.follow_burst_cooling_enabled_input.isChecked()
    cfg2 = dialog2.config()
    assert cfg2["batch_jitter_enabled"] is False
    assert cfg2["follow_burst_cooling_enabled"] is False

    # 3. apply_initial update
    dialog2.apply_initial(
        {
            "active": {
                "batch_jitter_enabled": True,
                "follow_burst_cooling_enabled": True,
            }
        }
    )
    assert dialog2.batch_jitter_enabled_input.isChecked()
    assert dialog2.follow_burst_cooling_enabled_input.isChecked()
    assert dialog2.config()["batch_jitter_enabled"] is True
    assert dialog2.config()["follow_burst_cooling_enabled"] is True
    dialog2.close()


def test_cold_start_warmup_settings_in_dialog():
    _application()
    # 1. Default state: enabled, min 45, max 60
    dialog = TaskConfigDialog()
    assert dialog.cold_start_warmup_enabled_input.isChecked()
    assert dialog.cold_start_warmup_min_input.isEnabled()
    assert dialog.cold_start_warmup_max_input.isEnabled()
    assert dialog.cold_start_warmup_min_input.value() == 45
    assert dialog.cold_start_warmup_max_input.value() == 60
    cfg = dialog.config()
    assert cfg["cold_start_warmup_enabled"] is True
    assert cfg["cold_start_warmup_min_seconds"] == 45
    assert cfg["cold_start_warmup_max_seconds"] == 60
    assert cfg["cold_start_warmup_duration_seconds"] == 60
    dialog.close()

    # 2. Enabled with custom duration
    dialog2 = TaskConfigDialog(
        {
            "cold_start_warmup_enabled": True,
            "cold_start_warmup_min_seconds": 60,
            "cold_start_warmup_max_seconds": 90,
            "cold_start_warmup_duration_seconds": 90,
        }
    )
    assert dialog2.cold_start_warmup_enabled_input.isChecked()
    assert dialog2.cold_start_warmup_min_input.isEnabled()
    assert dialog2.cold_start_warmup_max_input.isEnabled()
    assert dialog2.cold_start_warmup_min_input.value() == 60
    assert dialog2.cold_start_warmup_max_input.value() == 90
    cfg2 = dialog2.config()
    assert cfg2["cold_start_warmup_enabled"] is True
    assert cfg2["cold_start_warmup_min_seconds"] == 60
    assert cfg2["cold_start_warmup_max_seconds"] == 90
    assert cfg2["cold_start_warmup_duration_seconds"] == 90

    # 3. apply_initial toggle off
    dialog2.apply_initial({"active": {"cold_start_warmup_enabled": False, "cold_start_warmup_duration_seconds": 60}})
    assert not dialog2.cold_start_warmup_enabled_input.isChecked()
    assert not dialog2.cold_start_warmup_min_input.isEnabled()
    assert not dialog2.cold_start_warmup_max_input.isEnabled()
    assert dialog2.config()["cold_start_warmup_enabled"] is False
    dialog2.close()


def test_chain_hop_custom_ratio_and_depth_in_dialog():
    _application()
    # 1. Default state: disabled, ratio 0.70, depth 2
    dialog = TaskConfigDialog()
    assert not dialog.chain_hop_enabled_input.isChecked()
    assert not dialog.chain_hop_ratio_input.isEnabled()
    assert dialog.chain_hop_ratio_input.value() == 0.70
    assert not dialog.max_chain_depth_input.isEnabled()
    assert dialog.max_chain_depth_input.value() == 2
    cfg = dialog.config()
    assert cfg["chain_hop_enabled"] is False
    assert cfg["chain_hop_ratio"] == 0.70
    assert cfg["max_chain_depth"] == 2
    assert cfg["max_harvest_per_seed"] == 10
    dialog.close()

    # 2. Enabled with custom ratio & depth & harvest limit
    dialog2 = TaskConfigDialog(
        {
            "chain_hop_enabled": True,
            "chain_hop_ratio": 0.45,
            "max_chain_depth": 3,
            "max_harvest_per_seed": 4,
        }
    )
    assert dialog2.chain_hop_enabled_input.isChecked()
    assert dialog2.chain_hop_ratio_input.isEnabled()
    assert dialog2.chain_hop_ratio_input.value() == 0.45
    assert dialog2.max_chain_depth_input.isEnabled()
    assert dialog2.max_chain_depth_input.value() == 3
    assert dialog2.max_harvest_per_seed_input.isEnabled()
    assert dialog2.max_harvest_per_seed_input.value() == 4
    cfg2 = dialog2.config()
    assert cfg2["chain_hop_enabled"] is True
    assert cfg2["chain_hop_ratio"] == 0.45
    assert cfg2["max_chain_depth"] == 3
    assert cfg2["max_harvest_per_seed"] == 4

    # 3. Toggle off: controls should disable
    dialog2.chain_hop_enabled_input.setChecked(False)
    assert not dialog2.chain_hop_ratio_input.isEnabled()
    assert not dialog2.max_chain_depth_input.isEnabled()
    assert not dialog2.max_harvest_per_seed_input.isEnabled()
    assert dialog2.config()["chain_hop_enabled"] is False

    # 4. apply_initial update
    dialog2.apply_initial(
        {
            "active": {
                "chain_hop_enabled": True,
                "chain_hop_ratio": 0.85,
                "max_chain_depth": 1,
            }
        }
    )
    assert dialog2.chain_hop_enabled_input.isChecked()
    assert dialog2.chain_hop_ratio_input.isEnabled()
    assert dialog2.chain_hop_ratio_input.value() == 0.85
    assert dialog2.max_chain_depth_input.isEnabled()
    assert dialog2.max_chain_depth_input.value() == 1
    cfg3 = dialog2.config()
    assert cfg3["chain_hop_enabled"] is True
    assert cfg3["chain_hop_ratio"] == 0.85
    assert cfg3["max_chain_depth"] == 1
    dialog2.close()


def test_keyword_chain_fission_mode_in_dialog():
    _application()
    dialog = TaskConfigDialog()

    # 1. Switch outreach mode to keyword_chain
    idx = dialog.outreach_mode_input.findData("keyword_chain")
    assert idx >= 0
    dialog.outreach_mode_input.setCurrentIndex(idx)

    # Automatically enables chain hop with ratio 1.0 and depth 0 (infinite)
    # And locks controls with notice label
    assert dialog.chain_hop_enabled_input.isChecked()
    assert not dialog.chain_hop_enabled_input.isEnabled()
    assert dialog.chain_hop_ratio_input.value() == 1.0
    assert not dialog.chain_hop_ratio_input.isEnabled()
    assert dialog.max_chain_depth_input.value() == 0
    assert not dialog.max_chain_depth_input.isEnabled()
    assert not dialog.chain_hop_note_label.isHidden()
    assert "已由【无限顺藤摸瓜】模式全局接管" in dialog.chain_hop_note_label.text()

    cfg = dialog.config()
    assert cfg["outreach_mode"] == "keyword_chain"
    assert cfg["max_chain_depth"] == 0
    assert cfg["chain_hop_enabled"] is True
    assert cfg["chain_hop_ratio"] == 1.0

    # 2. Switch back to normal keyword mode
    kw_idx = dialog.outreach_mode_input.findData("keyword")
    dialog.outreach_mode_input.setCurrentIndex(kw_idx)
    assert dialog.chain_hop_enabled_input.isEnabled()
    assert dialog.chain_hop_ratio_input.isEnabled()
    assert dialog.max_chain_depth_input.isEnabled()
    assert dialog.chain_hop_note_label.isHidden()

    # 3. Loading saved config with keyword_chain and depth 10 (should be overridden to 0)
    dialog2 = TaskConfigDialog({
        "outreach_mode": "keyword_chain",
        "max_chain_depth": 10,
        "chain_hop_enabled": True,
        "chain_hop_ratio": 0.8,
    })
    assert dialog2.outreach_mode_input.currentData() == "keyword_chain"
    assert dialog2.max_chain_depth_input.value() == 0
    assert not dialog2.max_chain_depth_input.isEnabled()
    assert not dialog2.chain_hop_enabled_input.isEnabled()
    assert not dialog2.chain_hop_ratio_input.isEnabled()
    assert not dialog2.chain_hop_note_label.isHidden()

    cfg2 = dialog2.config()
    assert cfg2["outreach_mode"] == "keyword_chain"
    assert cfg2["max_chain_depth"] == 0
    assert cfg2["chain_hop_enabled"] is True
    assert cfg2["chain_hop_ratio"] == 1.0

    # 4. apply_initial with keyword_chain overrides depth to 0
    dialog3 = TaskConfigDialog()
    dialog3.apply_initial({
        "active": {
            "outreach_mode": "keyword_chain",
            "max_chain_depth": 5,
        }
    })
    assert dialog3.max_chain_depth_input.value() == 0
    assert not dialog3.max_chain_depth_input.isEnabled()
    assert not dialog3.chain_hop_note_label.isHidden()
    assert dialog3.config()["max_chain_depth"] == 0

    dialog.close()
    dialog2.close()
    dialog3.close()


def test_soft_landing_switch_in_dialog():
    _application()
    dialog = TaskConfigDialog()

    # Default is enabled
    assert dialog.soft_landing_enabled_input.isChecked()
    assert dialog.config()["enable_soft_landing"] is True
    assert dialog.config()["soft_landing_enabled"] is True

    # Toggle off
    dialog.soft_landing_enabled_input.setChecked(False)
    assert not dialog.soft_landing_enabled_input.isChecked()
    assert dialog.config()["enable_soft_landing"] is False

    # Apply initial with disabled
    dialog.apply_initial({"active": {"enable_soft_landing": False}})
    assert not dialog.soft_landing_enabled_input.isChecked()

    # Apply initial with enabled
    dialog.apply_initial({"active": {"soft_landing_enabled": True}})
    assert dialog.soft_landing_enabled_input.isChecked()

    dialog.close()


def test_target_followers_outreach_mode_and_controls():
    _application()
    dialog = TaskConfigDialog({
        "outreach_mode": "target_followers",
        "target_creators": ["@elonmusk", "OpenAI", "@sama"],
        "max_followers_per_target": 60,
        "action_min_delay": 5.0,
        "action_max_delay": 9.5,
    })

    assert dialog.outreach_mode_input.currentData() == "target_followers"
    assert not dialog.target_creators_widget.isHidden()
    assert not dialog.max_followers_per_target_input.isHidden()
    assert dialog.max_followers_per_target_input.value() == 60
    assert dialog.action_min_delay_input.value() == 5.0
    assert dialog.action_max_delay_input.value() == 9.5

    cfg = dialog.config()
    assert cfg["outreach_mode"] == "target_followers"
    assert cfg["target_creators"] == ["elonmusk", "OpenAI", "sama"]
    assert cfg["max_followers_per_target"] == 60
    assert cfg["action_min_delay"] == 5.0
    assert cfg["action_max_delay"] == 9.5

    dialog.close()


def test_blacklist_config_dialog_save_and_load(tmp_path, monkeypatch):
    from desktop.main_window import BlacklistConfigDialog
    _application()

    saved_payload = {}
    class FakeController:
        def get_blacklist_config(self):
            return {
                "blacklist_words": ["测试词1", "测试词2"],
                "check_options": {"check_name": True, "check_bio": False, "check_tweets": True},
            }
        def save_blacklist_config(self, data):
            nonlocal saved_payload
            saved_payload = data
            return data

    ctrl = FakeController()
    dialog = BlacklistConfigDialog(ctrl)

    assert "测试词1" in dialog.words_editor.toPlainText()
    assert dialog.check_name_cb.isChecked()
    assert not dialog.check_bio_cb.isChecked()
    assert dialog.check_tweets_cb.isChecked()

    # Modify and save
    dialog.words_editor.setPlainText("新词A\n新词B, 新词C")
    dialog.check_bio_cb.setChecked(True)

    # Mock QMessageBox.information to prevent modal blocking
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    dialog._save()

    assert saved_payload["blacklist_words"] == ["新词A", "新词B", "新词C"]
    assert saved_payload["check_options"] == {
        "check_name": True,
        "check_bio": True,
        "check_tweets": True,
    }
    dialog.close()


def test_task_config_dialog_ok_button_text_dynamic_change():
    _application()
    # 1. 默认 smart 模式 -> "保存智能时段"
    dialog = TaskConfigDialog(initial={"active": {"schedule_mode": "smart"}})
    assert dialog.ok_button is not None
    assert dialog.ok_button.text() == "保存智能时段"

    # 2. 切换到 "scheduled" -> "保存定时排期"
    sched_idx = dialog.schedule_mode_input.findData("scheduled")
    dialog.schedule_mode_input.setCurrentIndex(sched_idx)
    assert dialog.ok_button.text() == "保存定时排期"

    # 3. 切换到 "immediate" -> "保存并立即运行"
    imm_idx = dialog.schedule_mode_input.findData("immediate")
    dialog.schedule_mode_input.setCurrentIndex(imm_idx)
    assert dialog.ok_button.text() == "保存并立即运行"

def test_periodic_search_refresh_config_and_ui():
    _application()
    # 1. 默认开启，间隔默认为 15 分钟，且控件启用
    dialog = TaskConfigDialog()
    assert dialog.periodic_search_refresh_enabled_input.isChecked() is True
    assert dialog.search_refresh_interval_input.isEnabled() is True
    assert dialog.search_refresh_interval_input.value() == 15
    cfg = dialog.config()
    assert cfg["periodic_search_refresh_enabled"] is True
    assert cfg["search_refresh_interval_minutes"] == 15
    dialog.close()

    # 2. 传入开启及自定义时间
    dialog2 = TaskConfigDialog(
        initial={"active": {"periodic_search_refresh_enabled": True, "search_refresh_interval_minutes": 10}}
    )
    assert dialog2.periodic_search_refresh_enabled_input.isChecked() is True
    assert dialog2.search_refresh_interval_input.isEnabled() is True
    assert dialog2.search_refresh_interval_input.value() == 10
    cfg2 = dialog2.config()
    assert cfg2["periodic_search_refresh_enabled"] is True
    assert cfg2["search_refresh_interval_minutes"] == 10

    # 3. 动态切换开启/禁用
    dialog2.periodic_search_refresh_enabled_input.setChecked(False)
    assert dialog2.search_refresh_interval_input.isEnabled() is False
    assert dialog2.config()["periodic_search_refresh_enabled"] is False

    dialog2.periodic_search_refresh_enabled_input.setChecked(True)
    dialog2.search_refresh_interval_input.setValue(20)
    assert dialog2.search_refresh_interval_input.isEnabled() is True
    assert dialog2.config()["periodic_search_refresh_enabled"] is True
    assert dialog2.config()["search_refresh_interval_minutes"] == 20
    dialog2.close()


def test_smart_schedule_enabled_in_dialog():
    _application()
    # 1. 默认状态：开启 (True)
    dialog = TaskConfigDialog()
    assert dialog.smart_schedule_enabled_input.isChecked() is True
    cfg = dialog.config()
    assert cfg["smart_schedule_enabled"] is True
    dialog.close()

    # 2. 初始传入显式关闭 (False)
    dialog2 = TaskConfigDialog(
        initial={"active": {"smart_schedule_enabled": False}}
    )
    assert dialog2.smart_schedule_enabled_input.isChecked() is False
    cfg2 = dialog2.config()
    assert cfg2["smart_schedule_enabled"] is False

    # 3. 动态切换开启/关闭
    dialog2.smart_schedule_enabled_input.setChecked(True)
    assert dialog2.config()["smart_schedule_enabled"] is True

    dialog2.smart_schedule_enabled_input.setChecked(False)
    assert dialog2.config()["smart_schedule_enabled"] is False

    # 4. apply_initial 状态恢复
    dialog2.apply_initial({"active": {"smart_schedule_enabled": True}})
    assert dialog2.smart_schedule_enabled_input.isChecked() is True
    assert dialog2.config()["smart_schedule_enabled"] is True
    dialog2.close()


def test_profile_visit_ratio_in_dialog():
    _application()
    # 1. 默认状态：0.80 (80% 深入个人主页深度终审)
    dialog = TaskConfigDialog()
    assert abs(dialog.profile_visit_ratio_input.value() - 0.80) < 1e-4
    assert abs(dialog.config()["profile_visit_ratio"] - 0.80) < 1e-4
    dialog.close()

    # 2. 自定义初值 (如 1.00 强制进主页)
    dialog2 = TaskConfigDialog(initial={"active": {"profile_visit_ratio": 1.0}})
    assert abs(dialog2.profile_visit_ratio_input.value() - 1.0) < 1e-4
    assert abs(dialog2.config()["profile_visit_ratio"] - 1.0) < 1e-4

    # 3. 界面修改数值
    dialog2.profile_visit_ratio_input.setValue(0.85)
    assert abs(dialog2.config()["profile_visit_ratio"] - 0.85) < 1e-4

    # 4. 边界范围约束 0.0 ~ 1.0
    dialog2.profile_visit_ratio_input.setValue(1.5)
    assert dialog2.profile_visit_ratio_input.value() <= 1.0
    dialog2.profile_visit_ratio_input.setValue(-0.5)
    assert dialog2.profile_visit_ratio_input.value() >= 0.0

    # 5. apply_initial 异步动态载入
    dialog2.apply_initial({"active": {"profile_visit_ratio": 0.90}})
    assert abs(dialog2.profile_visit_ratio_input.value() - 0.90) < 1e-4
    assert abs(dialog2.config()["profile_visit_ratio"] - 0.90) < 1e-4
    dialog2.close()


def test_cdp_timezone_override_in_dialog():
    _application()
    # 1. 默认状态：开启 (True)，auto，且时区下拉框与 GPS 复选框处于可用状态
    dialog = TaskConfigDialog()
    assert dialog.cdp_timezone_override_enabled_input.isChecked() is True
    assert dialog.cdp_override_timezone_input.isEnabled() is True
    assert dialog.cdp_geolocation_override_enabled_input.isEnabled() is True
    assert dialog.cdp_geolocation_override_enabled_input.isChecked() is True
    assert dialog.config()["cdp_timezone_override_enabled"] is True
    assert dialog.config()["cdp_override_timezone"] == "auto"
    assert dialog.config()["cdp_geolocation_override_enabled"] is True
    dialog.close()

    # 2. 自定义初值 (如开启且指定日本东京)
    dialog2 = TaskConfigDialog(initial={"active": {"cdp_timezone_override_enabled": True, "cdp_override_timezone": "Asia/Tokyo", "cdp_geolocation_override_enabled": True}})
    assert dialog2.cdp_timezone_override_enabled_input.isChecked() is True
    assert dialog2.cdp_override_timezone_input.isEnabled() is True
    assert dialog2.cdp_geolocation_override_enabled_input.isEnabled() is True
    assert dialog2.cdp_override_timezone_input.currentData() == "Asia/Tokyo"
    assert dialog2.config()["cdp_timezone_override_enabled"] is True
    assert dialog2.config()["cdp_override_timezone"] == "Asia/Tokyo"
    assert dialog2.config()["cdp_geolocation_override_enabled"] is True

    # 3. 界面切换交互：切换时区为美国东部，关闭 GPS 经纬度
    tz_idx = dialog2.cdp_override_timezone_input.findData("America/New_York")
    dialog2.cdp_override_timezone_input.setCurrentIndex(tz_idx)
    dialog2.cdp_geolocation_override_enabled_input.setChecked(False)
    assert dialog2.config()["cdp_override_timezone"] == "America/New_York"
    assert dialog2.config()["cdp_geolocation_override_enabled"] is False

    # 4. 界面切换交互：取消勾选主开关关闭
    dialog2.cdp_timezone_override_enabled_input.setChecked(False)
    assert dialog2.cdp_override_timezone_input.isEnabled() is False
    assert dialog2.cdp_geolocation_override_enabled_input.isEnabled() is False
    assert dialog2.config()["cdp_timezone_override_enabled"] is False

    # 5. apply_initial 异步动态恢复
    dialog2.apply_initial({"active": {"cdp_timezone_override_enabled": True, "cdp_override_timezone": "Europe/London", "cdp_geolocation_override_enabled": True}})
    assert dialog2.cdp_timezone_override_enabled_input.isChecked() is True
    assert dialog2.cdp_override_timezone_input.isEnabled() is True
    assert dialog2.cdp_geolocation_override_enabled_input.isEnabled() is True
    assert dialog2.cdp_override_timezone_input.currentData() == "Europe/London"
    assert dialog2.config()["cdp_timezone_override_enabled"] is True
    assert dialog2.config()["cdp_override_timezone"] == "Europe/London"
    assert dialog2.config()["cdp_geolocation_override_enabled"] is True
    dialog2.close()


def test_graphql_risk_pause_configuration():
    """验证推特 GraphQL 业务风控自动熔断配置项的默认值、交互切换及数据导出."""
    _application()
    # 1. 默认情况下，graphql_risk_pause_enabled 默认为 True
    dialog = TaskConfigDialog()
    assert dialog.graphql_risk_pause_enabled_input.isChecked() is True
    assert dialog.config()["graphql_risk_pause_enabled"] is True
    dialog.close()

    # 2. 自定义初始值：用户关闭自动熔断
    dialog2 = TaskConfigDialog(initial={"active": {"graphql_risk_pause_enabled": False}})
    assert dialog2.graphql_risk_pause_enabled_input.isChecked() is False
    assert dialog2.config()["graphql_risk_pause_enabled"] is False

    # 3. 界面手动勾选开启
    dialog2.graphql_risk_pause_enabled_input.setChecked(True)
    assert dialog2.config()["graphql_risk_pause_enabled"] is True

    # 4. apply_initial 动态更新
    dialog2.apply_initial({"active": {"graphql_risk_pause_enabled": False}})
    assert dialog2.graphql_risk_pause_enabled_input.isChecked() is False
    assert dialog2.config()["graphql_risk_pause_enabled"] is False
    dialog2.close()


def test_scheme3a_and_scheme_ca_integration():
    """验证滑槽分段轨道胶囊与智能说明坞及零滚动条。"""
    from PySide6.QtCore import QEvent
    _application()

    dialog = TaskConfigDialog()
    dialog.resize(730, 820)
    dialog.show()

    # 1. 验证 Tab 0 滑槽分段胶囊按钮数量与双向同步
    assert len(dialog.schedule_pills._buttons) == 3
    dialog.schedule_pills._buttons[1].click()  # 立即执行
    assert dialog.schedule_mode_input.currentData() == "immediate"
    assert dialog.config()["schedule_mode"] == "immediate"
    assert "立即启动自动化" in dialog.schedule_mode_tip.text()

    dialog.schedule_pills._buttons[0].click()  # 智能时段
    assert dialog.schedule_mode_input.currentData() == "smart"
    assert dialog.config()["schedule_mode"] == "smart"

    # 2. 验证运行策略预设胶囊与徽章联动
    assert len(dialog.preset_pills._buttons) == 2
    dialog.preset_pills._buttons[1].click()  # Turbo 冲刺
    assert dialog.execution_preset_input.currentData() == "turbo"
    assert dialog.config()["execution_preset"] == "turbo"
    assert "300+" in dialog.preset_badge.text()

    dialog.preset_pills._buttons[0].click()  # 安全防风控
    assert dialog.execution_preset_input.currentData() == "safe"
    assert dialog.config()["execution_preset"] == "safe"

    # 3. 验证 Tab 1 智能说明坞 (Inspector Dock) 与鼠标悬浮事件响应
    enter_ev = QEvent(QEvent.Type.Enter)
    dialog.eventFilter(dialog.follow_burst_cooling_enabled_input, enter_ev)
    assert "高频爆发降频冷却" in dialog.dock_title_label.text()
    assert "90~150 秒" in dialog.dock_desc_label.text()

    dialog.eventFilter(dialog.cdp_timezone_override_enabled_input, enter_ev)
    assert "CDP 动态时区与语言覆盖" in dialog.dock_title_label.text()
    assert "UTC+8" in dialog.dock_desc_label.text()

    # 4. 验证运行配置 (Tab 0) 与安全限制 (Tab 1) 均彻底消除上下滚动条
    run_scroll = dialog.config_tabs.widget(0)
    safety_scroll = dialog.config_tabs.widget(1)
    assert run_scroll.verticalScrollBar().maximum() == 0
    assert safety_scroll.verticalScrollBar().maximum() == 0

    dialog.close()


def test_blacklist_filter_toggle_and_dialog(monkeypatch):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QMessageBox
    from desktop.main_window import BlacklistConfigDialog
    from agent.x_automation_engine import AutomationConfig, AccountFilterGuard

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    _application()

    # 1. 验证任务配置弹窗中敏感词过滤的默认状态与切换
    dialog = TaskConfigDialog()
    assert dialog.filter_blacklist_input.isChecked() is True
    assert dialog.config()["blacklist_filter_enabled"] is True

    dialog.filter_blacklist_input.setChecked(False)
    assert dialog.config()["blacklist_filter_enabled"] is False

    dialog.apply_initial({"active": {"blacklist_filter_enabled": True}})
    assert dialog.filter_blacklist_input.isChecked() is True
    dialog.close()

    # 2. 验证敏感词库配置弹窗的一键总开关联动
    mock_ctrl = MagicMock()
    mock_ctrl.get_blacklist_config.return_value = {
        "enabled": True,
        "blacklist_words": ["株", "注目すべき銘柄"],
        "check_options": {"check_name": True, "check_bio": True, "check_tweets": True},
    }

    dlg = BlacklistConfigDialog(mock_ctrl)
    assert dlg.blacklist_enabled_cb.isChecked() is True
    assert dlg.words_editor.isEnabled() is True
    assert dlg.check_name_cb.isEnabled() is True

    # 一键关闭
    dlg.blacklist_enabled_cb.setChecked(False)
    assert dlg.words_editor.isEnabled() is False
    assert dlg.check_name_cb.isEnabled() is False
    assert "一键停用中" in dlg.hint_label.text()

    # 保存并检查持久化数据
    dlg._save()
    mock_ctrl.save_blacklist_config.assert_called_once()
    saved_payload = mock_ctrl.save_blacklist_config.call_args[0][0]
    assert saved_payload["enabled"] is False
    dlg.close()

    # 3. 验证自动化引擎中一键关闭敏感词过滤时的放行逻辑
    cfg_disabled = AutomationConfig.from_mapping({
        "blacklist_filter_enabled": False,
        "blacklist_words": ["株"],
        "filter_verified_accounts": False,
        "filter_parody_accounts": False,
        "filter_bot_accounts": False,
    })
    should_skip, _ = AccountFilterGuard.check_candidate_account(
        name="日本株投資",
        handle="kabu_user",
        card_or_dom_text="",
        has_verified_badge=False,
        config=cfg_disabled,
    )
    assert should_skip is False, "当 blacklist_filter_enabled 为 False 时，严禁拦截敏感词"

    cfg_enabled = AutomationConfig.from_mapping({
        "blacklist_filter_enabled": True,
        "blacklist_words": ["株"],
        "filter_verified_accounts": False,
        "filter_parody_accounts": False,
        "filter_bot_accounts": False,
    })
    should_skip_en, reason = AccountFilterGuard.check_candidate_account(
        name="日本株投資",
        handle="kabu_user",
        card_or_dom_text="",
        has_verified_badge=False,
        config=cfg_enabled,
    )
    assert should_skip_en is True, "当 blacklist_filter_enabled 为 True 时，必须执行一票否决"
    assert "命中黑名单敏感词" in reason


def test_high_grade_anti_risk_enhancements():
    _application()

    # 1. 验证默认配置为全部开启（锦上添花高阶防封，默认保护）
    default_dlg = TaskConfigDialog()
    def_cfg = default_dlg.config()
    assert def_cfg["natural_roaming_enabled"] is True
    assert def_cfg["pre_click_guard_enabled"] is True
    assert def_cfg["search_pagination_refresh_enabled"] is True
    assert default_dlg.natural_roaming_enabled_input.isChecked() is True
    assert default_dlg.pre_click_guard_enabled_input.isChecked() is True
    assert default_dlg.search_pagination_refresh_enabled_input.isChecked() is True
    default_dlg.close()

    # 2. 验证自定义关闭与保存
    custom_dlg = TaskConfigDialog({
        "natural_roaming_enabled": False,
        "pre_click_guard_enabled": False,
        "search_pagination_refresh_enabled": False,
    })
    assert custom_dlg.natural_roaming_enabled_input.isChecked() is False
    assert custom_dlg.pre_click_guard_enabled_input.isChecked() is False
    assert custom_dlg.search_pagination_refresh_enabled_input.isChecked() is False
    c_cfg = custom_dlg.config()
    assert c_cfg["natural_roaming_enabled"] is False
    assert c_cfg["pre_click_guard_enabled"] is False
    assert c_cfg["search_pagination_refresh_enabled"] is False

    # 3. 验证动态交互勾选切换
    custom_dlg.natural_roaming_enabled_input.setChecked(True)
    custom_dlg.pre_click_guard_enabled_input.setChecked(True)
    custom_dlg.search_pagination_refresh_enabled_input.setChecked(True)
    updated_cfg = custom_dlg.config()
    assert updated_cfg["natural_roaming_enabled"] is True
    assert updated_cfg["pre_click_guard_enabled"] is True
    assert updated_cfg["search_pagination_refresh_enabled"] is True
    custom_dlg.close()

    # 4. 验证自动化引擎 AutomationConfig 字段与前置卡片质检过滤逻辑
    from agent.x_automation_engine import AutomationConfig
    auto_cfg = AutomationConfig.from_mapping({
        "natural_roaming_enabled": True,
        "pre_click_guard_enabled": True,
        "search_pagination_refresh_enabled": True,
    })
    assert auto_cfg.natural_roaming_enabled is True
    assert auto_cfg.pre_click_guard_enabled is True
    assert auto_cfg.search_pagination_refresh_enabled is True

    # 验证 pre_click_card_guard 对纯数字机器人 Handle 的毫秒级快速识别
    import asyncio
    from agent.x_automation_engine import XAutomationEngine

    class MockArticle:
        async def query_selector(self, sel):
            return None
        async def inner_text(self):
            return "正常的生活推文内容"

    loop = asyncio.new_event_loop()
    try:
        # 纯数字尾巴机器人
        bad, reason = loop.run_until_complete(
            XAutomationEngine._pre_click_card_guard(
                MockArticle(),
                "crypto_bot_198237498",
                config=AutomationConfig(smart_newbie_recognition_enabled=False),
            )
        )
        assert bad is True
        assert "超长连续数字后缀" in reason

        # 正常账号
        good, _ = loop.run_until_complete(
            XAutomationEngine._pre_click_card_guard(MockArticle(), "alice_tokyo")
        )
        assert good is False
    finally:
        loop.close()


def test_graphql_scout_filter_option1() -> None:
    """验证底层数据流极速透视预筛：默认开启、自由勾选、配置持久化以及内存秒级打分质检."""
    _application()
    from desktop.main_window import TaskConfigDialog
    from agent.x_automation_engine import AutomationConfig, XAutomationEngine

    # 1. 验证默认配置为 True（底层数据流极速透视）
    default_dlg = TaskConfigDialog()
    def_cfg = default_dlg.config()
    assert def_cfg.get("graphql_scout_filter_enabled") is True
    assert default_dlg.graphql_scout_filter_enabled_input.isChecked() is True
    default_dlg.close()

    # 2. 验证传入 False 时正确加载与导出
    custom_dlg = TaskConfigDialog({"graphql_scout_filter_enabled": False})
    assert custom_dlg.graphql_scout_filter_enabled_input.isChecked() is False
    assert custom_dlg.config()["graphql_scout_filter_enabled"] is False

    # 3. 验证动态勾选切换
    custom_dlg.graphql_scout_filter_enabled_input.setChecked(True)
    assert custom_dlg.config()["graphql_scout_filter_enabled"] is True
    custom_dlg.graphql_scout_filter_enabled_input.setChecked(False)
    assert custom_dlg.config()["graphql_scout_filter_enabled"] is False
    custom_dlg.close()

    # 4. 验证 AutomationConfig 默认值为 True
    cfg_def = AutomationConfig()
    assert cfg_def.graphql_scout_filter_enabled is True

    cfg_active = AutomationConfig.from_mapping({"graphql_scout_filter_enabled": True, "max_follower_threshold": 3000})
    assert cfg_active.graphql_scout_filter_enabled is True
    assert cfg_active.max_follower_threshold == 3000

    # 5. 验证底层数据流透视预筛与内存秒级质检逻辑
    engine = XAutomationEngine(cdp_url="http://127.0.0.1:9222")

    mock_search_timeline_data = {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {
                        "instructions": [
                            {
                                "type": "TimelineAddEntries",
                                "entries": [
                                    {
                                        "entryId": "tweet-1",
                                        "content": {
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "__typename": "Tweet",
                                                        "core": {
                                                            "user_results": {
                                                                "result": {
                                                                    "legacy": {
                                                                        "screen_name": "qualified_user",
                                                                        "name": "Alex Tech",
                                                                        "description": "Tech builder & thinker",
                                                                        "followers_count": 800,
                                                                        "friends_count": 200,
                                                                        "following": False,
                                                                        "verified": False,
                                                                        "protected": False,
                                                                        "default_profile_image": False,
                                                                    }
                                                                }
                                                            }
                                                        },
                                                        "legacy": {"full_text": "Building great web tools!"}
                                                    }
                                                }
                                            }
                                        }
                                    },
                                    {
                                        "entryId": "tweet-2",
                                        "content": {
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "__typename": "Tweet",
                                                        "core": {
                                                            "user_results": {
                                                                "result": {
                                                                    "legacy": {
                                                                        "screen_name": "giant_celebrity",
                                                                        "name": "Big Celebrity",
                                                                        "description": "Public figure",
                                                                        "followers_count": 500000,
                                                                        "friends_count": 100,
                                                                        "following": False,
                                                                        "verified": False,
                                                                        "protected": False,
                                                                    }
                                                                }
                                                            }
                                                        },
                                                        "legacy": {"full_text": "Hello fans"}
                                                    }
                                                }
                                            }
                                        }
                                    },
                                    {
                                        "entryId": "tweet-3",
                                        "content": {
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "__typename": "Tweet",
                                                        "core": {
                                                            "user_results": {
                                                                "result": {
                                                                    "legacy": {
                                                                        "screen_name": "already_following_user",
                                                                        "name": "Friend",
                                                                        "followers_count": 500,
                                                                        "friends_count": 100,
                                                                        "following": True,
                                                                    }
                                                                }
                                                            }
                                                        },
                                                        "legacy": {"full_text": "Good morning"}
                                                    }
                                                }
                                            }
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }

    # 执行解析与质检
    items_count = engine._parse_and_cache_scout_candidates(mock_search_timeline_data, cfg_active)
    assert items_count == 3

    # 验证 user_cache 全局更新
    assert "qualified_user" in engine.user_cache
    assert engine.user_cache["qualified_user"]["followers_count"] == 800

    # 验证合格博主进入 _scout_candidates
    assert "qualified_user" in engine._scout_candidates
    assert engine._scout_candidates["qualified_user"]["name"] == "Alex Tech"

    # 验证超粉丝限制博主与已关注博主进入 _scout_rejected_handles
    assert "giant_celebrity" in engine._scout_rejected_handles
    assert "already_following_user" in engine._scout_rejected_handles
    assert "超出预设门槛" in engine._scout_rejection_reasons["giant_celebrity"]
    assert "已在关注中" in engine._scout_rejection_reasons["already_following_user"]


def test_blacklist_config_dialog_matrix_and_quick_add(monkeypatch):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QMessageBox
    from desktop.main_window import BlacklistConfigDialog

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    _application()

    mock_ctrl = MagicMock()
    mock_ctrl.get_blacklist_config.return_value = {
        "enabled": True,
        "blacklist_words": ["株式投資", "サブ垢"],
        "check_options": {"check_name": True, "check_bio": True, "check_tweets": True},
    }

    dlg = BlacklistConfigDialog(mock_ctrl)

    # 1. 验证初始分类归并
    cats = dlg._categorize()
    assert "株式投資" in cats["finance"]
    assert "サブ垢" in cats["ol"]
    assert len(cats["custom"]) == 0

    # 2. 验证快捷录入与回车自动去重排序
    dlg.quick_add_input.setText("高額配当, 副業詐欺，株式投資")
    dlg._on_add_words()

    assert "高額配当" in dlg._words
    assert "副業詐欺" in dlg._words
    # 验证去重
    assert dlg._words.count("株式投資") == 1
    # 验证自动排序
    assert dlg._words == sorted(dlg._words)

    # 验证新词自动归入 custom
    cats2 = dlg._categorize()
    assert "高額配当" in cats2["custom"]
    assert "副業詐欺" in cats2["custom"]

    # 3. 验证删除词条 (右上角角标 X)
    dlg._delete_word("高額配当")
    assert "高額配当" not in dlg._words
    assert "副業詐欺" in dlg._words

    # 4. 验证分类胶囊筛选切换
    dlg._set_active_filter("custom")
    assert dlg._active_filter == "custom"
    dlg._set_active_filter("all")
    assert dlg._active_filter == "all"

    # 5. 验证视图模式切换 (气泡流 ⇄ 纯文本)
    assert not dlg._is_text_mode
    dlg._toggle_view_mode()
    assert dlg._is_text_mode
    assert not dlg.words_editor.isHidden()
    assert dlg.matrix_scroll.isHidden()

    dlg.words_editor.setPlainText("测试A\n测试B")
    dlg._toggle_view_mode()
    assert not dlg._is_text_mode
    assert "测试A" in dlg._words
    assert "测试B" in dlg._words

    # 6. 保存并验证回传
    dlg._save()
    mock_ctrl.save_blacklist_config.assert_called_once()
    saved = mock_ctrl.save_blacklist_config.call_args[0][0]
    assert "测试A" in saved["blacklist_words"]
    assert "测试B" in saved["blacklist_words"]
    dlg.close()


def test_block_video_streams_dialog_config():
    _application()

    # 1. 默认无传参新建：默认勾选开启
    dialog_default = TaskConfigDialog()
    assert hasattr(dialog_default, "block_video_streams_input")
    assert dialog_default.block_video_streams_input.isChecked() is True
    assert dialog_default.config()["block_video_streams"] is True
    dialog_default.close()

    # 2. 载入历史配置：明确为 False 时取消勾选
    dialog_off = TaskConfigDialog({"block_video_streams": False})
    assert dialog_off.block_video_streams_input.isChecked() is False
    assert dialog_off.config()["block_video_streams"] is False

    # 3. 动态手动点击切换
    dialog_off.block_video_streams_input.setChecked(True)
    assert dialog_off.config()["block_video_streams"] is True
    dialog_off.block_video_streams_input.setChecked(False)
    assert dialog_off.config()["block_video_streams"] is False
    dialog_off.close()







