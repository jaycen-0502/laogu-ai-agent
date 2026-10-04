# -*- coding: utf-8 -*-
import pytest
from agent.blacklist_filter import check_user_blacklist, DEFAULT_BLACKLIST_WORDS
from agent.x_automation_engine import AccountFilterGuard, AutomationConfig


def test_default_blacklist_contains_demographic_and_evasion_words():
    # 验证扩充后的默认词库包含 00后、00line 等关键特征词
    assert "00后" in DEFAULT_BLACKLIST_WORDS
    assert "00line" in DEFAULT_BLACKLIST_WORDS
    assert "JK" in DEFAULT_BLACKLIST_WORDS
    assert "JD" in DEFAULT_BLACKLIST_WORDS
    assert "学生" in DEFAULT_BLACKLIST_WORDS
    assert "兼职" in DEFAULT_BLACKLIST_WORDS
    assert "裏垢" in DEFAULT_BLACKLIST_WORDS
    assert "パパ活" in DEFAULT_BLACKLIST_WORDS


def test_tweet_blacklist_screening_blocks_bad_posts():
    # 测试推文流前置黑名单过滤逻辑
    cfg = AutomationConfig.from_mapping({
        "blacklist_words": ["00后", "兼职", "00line", "JK"],
        "check_options": {"check_tweets": True},
    })

    # 1. 命中推文
    bad_tweet_1 = "大二 00后女生在读，求同好交流！"
    is_bl, hit_word = check_user_blacklist(
        {"display_name": "", "screen_name": "", "bio": "", "recent_tweets": [bad_tweet_1]},
        cfg.blacklist_words,
        {"check_name": False, "check_bio": False, "check_tweets": True},
    )
    assert is_bl is True
    assert hit_word == "00后"

    # 2. 命中日文推文
    bad_tweet_2 = "00line 女子大生です。よろしくね！"
    is_bl2, hit_word2 = check_user_blacklist(
        {"display_name": "", "screen_name": "", "bio": "", "recent_tweets": [bad_tweet_2]},
        cfg.blacklist_words,
        {"check_name": False, "check_bio": False, "check_tweets": True},
    )
    assert is_bl2 is True
    assert hit_word2 == "00line"

    # 3. 正常正常科技讨论推文，不受影响
    clean_tweet = "今天测试了深度学习新模型，准确率提升了 3.5%，效果非常棒。"
    is_bl3, _ = check_user_blacklist(
        {"display_name": "", "screen_name": "", "bio": "", "recent_tweets": [clean_tweet]},
        cfg.blacklist_words,
        {"check_name": False, "check_bio": False, "check_tweets": True},
    )
    assert is_bl3 is False


def test_hovercard_dom_fallback_blocks_candidate_even_without_graphql_cache():
    # 模拟 GraphQL 缓存缺失，但 DOM 名片抓取到了 00后 / 兼职 等简介
    cfg = AutomationConfig.from_mapping({
        "blacklist_words": ["00后", "兼职", "裏垢"],
        "check_options": {"check_bio": True, "check_name": True},
    })

    # 假设 user_info 缓存为空 (未拦截到 GraphQL)
    cached_empty_desc = ""
    # 1. 测试仅在自定义黑名单中的词 (如 00后) 通过 DOM 名片文本成功拦截
    hover_dom_text_1 = "小美\n@xiaomei_00\n00后大二在校生，平时喜欢数码摄影"
    combined_card_text_1 = f"{cached_empty_desc} {hover_dom_text_1}".strip()
    candidate_name_1 = hover_dom_text_1.split("\n")[0]

    is_bad_1, reason_1 = AccountFilterGuard.check_candidate_account(
        name=candidate_name_1,
        handle="xiaomei_00",
        card_or_dom_text=combined_card_text_1,
        has_verified_badge=False,
        config=cfg,
    )

    assert is_bad_1 is True
    assert "黑名单敏感词" in reason_1
    assert "00后" in reason_1

    # 2. 测试内置 NSFW/广告敏感词 (如 兼职) 通过 DOM 名片文本拦截
    hover_dom_text_2 = "小红\n@xiaohong\n兼职模特与摄影爱好者"
    is_bad_2, reason_2 = AccountFilterGuard.check_candidate_account(
        name="小红",
        handle="xiaohong",
        card_or_dom_text=hover_dom_text_2,
        has_verified_badge=False,
        config=cfg,
    )

    assert is_bad_2 is True
    assert "兼职" in reason_2
