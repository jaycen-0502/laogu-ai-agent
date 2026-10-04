# -*- coding: utf-8 -*-
import pytest
from agent.blacklist_filter import (
    DEFAULT_BLACKLIST_WORDS,
    DEFAULT_CHECK_OPTIONS,
    check_user_blacklist,
    clean_target_creators,
    parse_blacklist_input,
)


def test_empty_blacklist():
    user = {"display_name": "Alice", "screen_name": "alice_stock", "bio": "Investor", "recent_tweets": ["株の話"]}
    assert check_user_blacklist(user, []) == (False, "")
    assert check_user_blacklist(user, None) == (False, "")


def test_empty_user_data():
    assert check_user_blacklist({}, ["株"]) == (False, "")
    assert check_user_blacklist({"display_name": "", "screen_name": ""}, ["株"]) == (False, "")


def test_hit_display_name():
    user = {"display_name": "日本株ハンター", "screen_name": "hunter99", "bio": "hello", "recent_tweets": []}
    hit, word = check_user_blacklist(user, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert word in ["日本株", "株"]


def test_hit_screen_name():
    user = {"display_name": "John Doe", "screen_name": "ai_semi_jp", "bio": "Tech", "recent_tweets": []}
    hit, word = check_user_blacklist(user, ["semi", "bot"])
    assert hit is True
    assert word == "semi"


def test_hit_bio():
    user = {
        "display_name": "佐藤",
        "screen_name": "sato_123",
        "bio": "コツコツと資産を形成しています。日々の学びを記録中。",
        "recent_tweets": ["おはようございます！"],
    }
    hit, word = check_user_blacklist(user, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert word == "資産を形成"


def test_hit_recent_tweets_list():
    user = {
        "display_name": "田中",
        "screen_name": "tanaka_daily",
        "bio": "カフェ巡りと読書が趣味",
        "recent_tweets": [
            "今日のランチ美味しかった！",
            "注目すべき銘柄をいくつか分析してみました。",
            "明日も晴れるといいな",
        ],
    }
    hit, word = check_user_blacklist(user, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert word in ["注目すべき銘柄", "銘柄"]


def test_hit_recent_tweets_string():
    user = {
        "display_name": "Bob",
        "screen_name": "bob_x",
        "bio": "Nothing special",
        "recent_tweets": "最新の半導体市場の動向について解説します",
    }
    hit, word = check_user_blacklist(user, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert word == "半導体"


def test_check_options_filtering():
    user = {
        "display_name": "株マスター",
        "screen_name": "kabu_taro",
        "bio": "半導体関連の動向",
        "recent_tweets": ["AI関連のニュースまとめ"],
    }
    # If check_name is False, name & screen_name shouldn't trigger
    opts1 = {"check_name": False, "check_bio": False, "check_tweets": False}
    assert check_user_blacklist(user, ["株", "半導体", "ai関連"], opts1) == (False, "")

    # Enable only tweets check
    opts2 = {"check_name": False, "check_bio": False, "check_tweets": True}
    hit, word = check_user_blacklist(user, ["ai関連"], opts2)
    assert hit is True
    assert word == "ai関連"

    # Disable tweets check, enable bio check
    opts3 = {"check_name": False, "check_bio": True, "check_tweets": False}
    hit, word = check_user_blacklist(user, ["半導体"], opts3)
    assert hit is True
    assert word == "半導体"


def test_case_insensitive_matching():
    user = {
        "display_name": "Tech Enthusiast",
        "screen_name": "geek_girl",
        "bio": "Focus on AI関連 and Robotics",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user, ["ai関連"])
    assert hit is True


def test_clean_target_creators():
    raw = "@elonmusk, @openai\nsama\n\n@elonmusk, @BILLGATES  ;  sundarpichai"
    result = clean_target_creators(raw)
    assert result == ["elonmusk", "openai", "sama", "BILLGATES", "sundarpichai"]

    # Test list input
    assert clean_target_creators(["@user1", "user2, @user3"]) == ["user1", "user2", "user3"]
    assert clean_target_creators("") == []
    assert clean_target_creators(None) == []


def test_parse_blacklist_input():
    raw = "株\n注目すべき銘柄, テーマバリュー，資産を形成;日本株\n\n株"
    parsed = parse_blacklist_input(raw)
    assert parsed == ["株", "注目すべき銘柄", "テーマバリュー", "資産を形成", "日本株"]
    assert parse_blacklist_input("") == []


def test_nfkc_normalization_full_width():
    # 1. Full-width AI (\uff21\uff29) in bio matching half-width 'ai' in blacklist
    user1 = {
        "display_name": "副業紹介",
        "screen_name": "side_biz",
        "bio": "最新の\uff21\uff29副業で月収アップ！",  # ＡＩ
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user1, ["AI"])
    assert hit is True
    assert word == "AI"

    # 2. Full-width FX in name matching 'fx' in blacklist
    user2 = {
        "display_name": "\uff26\uff38自動売買",  # ＦＸ
        "screen_name": "fx_trader",
        "bio": "一般のトレーダーです",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user2, ["fx"])
    assert hit is True

    # 3. Clean user not matching
    user3 = {
        "display_name": "日常つぶやき",
        "screen_name": "daily_life",
        "bio": "コーヒーが好きです",
        "recent_tweets": ["いい天気ですね"],
    }
    hit, word = check_user_blacklist(user3, ["AI", "株", "投資", "FX"])
    assert hit is False
    assert word == ""


def test_benign_corporate_and_memory_terms_protection():
    # 1. 正常日本职场人简介带“株式会社”，不应误中单字“株”
    salaryman = {
        "display_name": "山田",
        "screen_name": "yamada_dev",
        "bio": "株式会社サイバーでエンジニアをしています。趣味はキャンプと読書。",
        "recent_tweets": ["今日の技術カンファレンス最高だった！"],
    }
    hit, word = check_user_blacklist(salaryman, ["株"])
    assert hit is False
    assert word == ""

    # 2. 正常日本生活推文带“株主優待”，不应误中单字“株”
    perk_lover = {
        "display_name": "さくら",
        "screen_name": "sakura_daily",
        "bio": "日々の美味しいご飯と日常記録",
        "recent_tweets": ["マクドナルドの株主優待使ってバーガー食べてきた！美味しかった〜"],
    }
    hit, word = check_user_blacklist(perk_lover, ["株"])
    assert hit is False
    assert word == ""

    # 3. 正常成年人回忆“学生時代”，不应误中“学生”
    alumnus = {
        "display_name": "健太",
        "screen_name": "kenta_run",
        "bio": "学生時代はサッカー部で全国目指してました。今は都内勤務の社会人3年目。",
        "recent_tweets": ["週末ハーフマラソン走ってきます！"],
    }
    hit, word = check_user_blacklist(alumnus, ["学生"])
    assert hit is False
    assert word == ""

    # 4. 真正炒股推广的号，仍能精准命中“株”
    stock_promoter = {
        "display_name": "億り人への道",
        "screen_name": "investor_jp",
        "bio": "日本株と米国株で1億円達成。毎日おすすめ株を呟きます。",
        "recent_tweets": ["テンバガー候補の株を紹介します"],
    }
    hit, word = check_user_blacklist(stock_promoter, ["株"])
    assert hit is True
    assert word == "株"

    # 5. 真正现役学生号，仍能精准命中“学生”
    current_student = {
        "display_name": "みく",
        "screen_name": "miku_campus",
        "bio": "都内の大学に通う現役学生です！仲良くしてください〜",
        "recent_tweets": ["レポート終わらない泣いた"],
    }
    hit, word = check_user_blacklist(current_student, ["学生"])
    assert hit is True
    assert word == "学生"


def test_blacklist_comment_parsing():
    raw_input = """
    # === 分类注释 1 ===
    サブ垢
    さぶあか
    # 这一行是注释
    え○ち
    """
    parsed = parse_blacklist_input(raw_input)
    assert "サブ垢" in parsed
    assert "さぶあか" in parsed
    assert "え○ち" in parsed
    assert not any(w.startswith("#") for w in parsed)


def test_japanese_fake_ol_bait_detection():
    # Account 1: Ecchi bait
    user_ecchi = {
        "display_name": "くるるん",
        "screen_name": "kurukuru3c",
        "bio": "24歳OL たまにちょっとえ○ちな一面も... 甘えたい夜は大人の男性とお話しするのが好き",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user_ecchi, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert word in ["え○ち", "大人の男性", "甘えたい夜"]

    # Account 2: Sub-account bait
    user_sub = {
        "display_name": "かえで、サブ",
        "screen_name": "kaede___ede",
        "bio": "新卒OL Iカップ さぶあかです 話したいときはこっち",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user_sub, DEFAULT_BLACKLIST_WORDS)
    assert hit is True

    # Account 3: Sugar dating / papa katsu bait
    user_sugar = {
        "display_name": "まお",
        "screen_name": "mao_2nu",
        "bio": "21歳 年上のおじさまがタイプ サブ: @mao_2vf",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(user_sugar, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert word in ["年上のおじさま", "サブ:"] or "年上" in word


def test_cup_size_safe_regex_protection():
    # Safe user: Watching World Cup or eating cup noodles -> must NOT hit!
    safe_user_world_cup = {
        "display_name": "サッカーファン",
        "screen_name": "soccer_jp",
        "bio": "ワールドカップの応援中！カップ麺食べながら観戦します。お気に入りのマグカップ。",
        "recent_tweets": ["日本代表がんばれ！"],
    }
    hit, word = check_user_blacklist(safe_user_world_cup, DEFAULT_BLACKLIST_WORDS)
    assert hit is False

    # Erotic bait user with cup size
    bait_user = {
        "display_name": "ゆな",
        "screen_name": "yuna_secret",
        "bio": "都内一人暮らし Gカップです DM待ってます",
        "recent_tweets": [],
    }
    hit, word = check_user_blacklist(bait_user, DEFAULT_BLACKLIST_WORDS)
    assert hit is True
    assert "カップ" in word


