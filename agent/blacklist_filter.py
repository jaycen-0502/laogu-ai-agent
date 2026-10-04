# -*- coding: utf-8 -*-
"""Dynamic full-field keyword filter engine and target creator utilities.

Provides unified blacklist inspection across display name, screen name, bio,
and recent tweets with a strict one-vote veto policy.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


DEFAULT_BLACKLIST_CATEGORIES: dict[str, list[str]] = {
    "日区假冒OL/擦边引流/援交色情 (えち垢/引流矩阵)": [
        "サブ垢",
        "さぶあか",
        "サブあか",
        "sub垢",
        "え○ち",
        "えっち",
        "エッチ",
        "年上のおじさま",
        "大人の男性",
        "年上の男性が好き",
        "甘えたい夜",
        "ヨシヨシしてほしい",
        "かまってほしい",
        "すぐ懐きます",
        "懐いちゃいます",
        "セフレ",
        "オナニー",
        "ファンティア",
        "myfans",
        "マイファンズ",
        "Iカップ",
        "Hカップ",
        "Gカップ",
        "Fカップ",
        "裏垢",
        "裏アカ",
        "裏垢女子",
        "裏垢男子",
        "パパ活",
        "ママ活",
        "オフパコ",
        "出会い",
        "マン凸",
        "パイ凸",
    ],
    "金融灰产与荐股诈骗精准排雷（杜绝误伤株式会社与株主优待）": [
        "株式投資",
        "投資勧誘",
        "急騰銘柄",
        "自動売買",
        "爆益",
        "銘柄配信",
        "注目すべき銘柄",
        "テーマバリュー",
        "資産を形成",
        "実践的な投資手法",
        "日本株",
        "半導体",
        "AI関連",
    ],
    "未成年/学生/涉黄灰产": [
        "00后",
        "00line",
        "01line",
        "02line",
        "03line",
        "04line",
        "05line",
        "00年",
        "00世代",
        "学生",
        "在读",
        "学妹",
        "JK",
        "JD",
        "兼职",
        "同城",
        "空降",
        "福利姬",
        "微密圈",
    ],
}

DEFAULT_BLACKLIST_WORDS: list[str] = [
    word for word_list in DEFAULT_BLACKLIST_CATEGORIES.values() for word in word_list
]

DEFAULT_CHECK_OPTIONS: dict[str, bool] = {
    "check_name": True,
    "check_bio": True,
    "check_tweets": True,
}


def normalize_text(text: str) -> str:
    """Normalize text using Unicode NFKC form and lower-casing.

    Transforms full-width characters (e.g. 'ＡＩ' -> 'AI', '１２３' -> '123')
    and ideographic spaces into standard ASCII/half-width equivalents for robust matching.
    """
    if not text:
        return ""
    return unicodedata.normalize("NFKC", str(text)).lower()


def clean_target_creators(raw_input: str | Iterable[str] | None) -> list[str]:
    """Clean and parse target creator handles/IDs from text or list.

    Supports comma, newline, Chinese comma, and whitespace separators.
    Automatically strips '@' prefixes and whitespace, removing duplicates
    while preserving insertion order.
    """
    if not raw_input:
        return []

    tokens: list[str] = []
    if isinstance(raw_input, str):
        raw_items = re.split(r'[\r\n,，;；\s]+', raw_input)
        tokens = [item for item in raw_items if item]
    elif isinstance(raw_input, (list, tuple, set)):
        for item in raw_input:
            if isinstance(item, str):
                parts = re.split(r'[\r\n,，;；\s]+', item)
                tokens.extend(p for p in parts if p)

    cleaned_list: list[str] = []
    seen: set[str] = set()

    for item in tokens:
        clean = item.strip().lstrip("@").strip()
        if clean:
            lower = clean.lower()
            if lower not in seen:
                seen.add(lower)
                cleaned_list.append(clean)

    return cleaned_list


def parse_blacklist_input(raw_input: str | Iterable[str] | None) -> list[str]:
    """Parse blacklist keywords from multi-line or comma-separated input.

    Preserves case for display while deduplicating case-insensitively.
    """
    if not raw_input:
        return []

    tokens: list[str] = []
    if isinstance(raw_input, str):
        raw_items = re.split(r'[\r\n,，;；]+', raw_input)
        tokens = [item for item in raw_items if item]
    elif isinstance(raw_input, (list, tuple, set)):
        for item in raw_input:
            if isinstance(item, str):
                parts = re.split(r'[\r\n,，;；]+', item)
                tokens.extend(p for p in parts if p)

    cleaned: list[str] = []
    seen: set[str] = set()

    for item in tokens:
        word = item.strip()
        if word and not word.startswith("#"):
            key = normalize_text(word).strip()
            if key not in seen:
                seen.add(key)
                cleaned.append(word)

    return cleaned


def check_user_blacklist(
    user_data: dict[str, Any],
    blacklist_words: list[str] | None,
    check_options: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    统一校验用户所有公开信息。
    只要命中任意一个词，立即终止并返回 (True, 命中的具体词)。
    采用 Unicode NFKC 全半角归一化，杜绝日区全角/变体字符绕过。
    """
    if not blacklist_words:
        return False, ""

    check_opts = check_options or DEFAULT_CHECK_OPTIONS

    # 1. 提取待检查的文本池
    fields_to_check: list[str] = []

    if check_opts.get("check_name", True):
        fields_to_check.append(str(user_data.get("display_name") or ""))
        fields_to_check.append(str(user_data.get("screen_name") or ""))

    if check_opts.get("check_bio", True):
        fields_to_check.append(str(user_data.get("bio") or ""))

    if check_opts.get("check_tweets", True):
        # 兼容最新推文列表或单条字符串
        tweets = user_data.get("recent_tweets", [])
        if isinstance(tweets, list):
            fields_to_check.extend(str(t) for t in tweets if t)
        elif tweets:
            fields_to_check.append(str(tweets))

    # 合并清洗后的全文字符串（进行 Unicode NFKC 归一化，全角转半角，消除变体差异）
    combined_raw = " ".join([text for text in fields_to_check if text])
    combined_text = normalize_text(combined_raw)

    if not combined_text:
        return False, ""

    # 2. 遍历黑名单，一票否决
    for word in blacklist_words:
        clean_word = normalize_text(str(word)).strip()
        if not clean_word:
            continue

        # 针对极短纯 ASCII 关键词（<= 3 个字符，例如 'ai', 'fx', 'dm'）：
        # 增加英文字母边界保护，避免误伤内嵌包含该子串的常用英文词（如 'daily', 'email', 'train', 'prefix'）
        # 同时完美支持日文语境（如 '最新のAI副業'）及下划线账号（如 'ai_bot', 'fx_trader'）
        if re.fullmatch(r"[a-z0-9]+", clean_word) and len(clean_word) <= 3:
            pattern = r"(?<![a-z])" + re.escape(clean_word) + r"(?![a-z])"
            if re.search(pattern, combined_text):
                return True, str(word).strip()
        elif clean_word == "株":
            # 针对单汉字 "株"，保护常见良性企业词汇（如 "株式会社"、"株式會社"、"株主優待"）
            text_without_safe_corp = re.sub(r"株式(?:会社|會社)|株主優待", "", combined_text)
            if "株" in text_without_safe_corp:
                return True, str(word).strip()
        elif clean_word == "学生":
            # 针对 "学生"，保护成年人正常回忆词汇（如 "学生時代"、"学生の頃"、"学生のころ"）
            text_without_memories = re.sub(r"学生時代|学生の頃|学生のころ", "", combined_text)
            if "学生" in text_without_memories:
                return True, str(word).strip()
        else:
            if clean_word in combined_text:
                return True, str(word).strip()  # 命中黑名单，返回拦截标记与命中词

    # 3. 罩杯性暗示智能拦截（精准识别 Iカップ, Fカップ, Gカップ, Hカップ, Dカップ，严格避开 ワールドカップ, カップ麺, マグカップ）
    cup_match = re.search(r"(?<![a-z0-9])[a-z]カップ", combined_text)
    if cup_match:
        return True, cup_match.group(0).upper()

    return False, ""
