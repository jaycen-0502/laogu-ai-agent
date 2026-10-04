# -*- coding: utf-8 -*-
"""
authenticity_learner.py - 老谷 AI 控制中心专属：真人度自学习与特征强化闭环引擎
特性：
1. 零红线脱敏 (Base64 隔离违规词，0ms 快速本地拦截追星号、广告号、色情号)
2. 日本真实成年生活锚点词提取 (餐饮、职场、爱好、通勤)
3. 动态特征权重数据库 (SQLite WAL 模式，高并发安全)
4. 48 小时回关强化学习闭环 (回关 Reward +3，未回关 Penalty -1)
5. 专为老谷控制中心 CDP Playwright 设计，无感热插拔与开关控制
"""

import os
import sys
import re
import base64
import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Tuple, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 本地特征学习记忆库路径
LEARNING_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'laogu_learning_store.db'))

class SafeVocabulary:
    """Base64 本地红线词库，确保代码零明文、零红线触碰"""
    # 追星/饭圈/二手周边交易
    FANDOM_B64 = [
        "MDRsaW5l", "5ZCM5ouF5ouS5ZCm", "5ZCM5ouF5q2T6L+O", "5LuW5ouF", "55WM55Wp",
        "5Y+W5byV55So", "5Lqk5o2i5ZKi", "6K2y5rih", "5a6a5L6h", "5Y+C5oim", "5o6o44GX5LqL",
        "44Kw44OD44K65Lqk5o2i", "6YqA44OG", "55eb44OQ"
    ]
    # 广告/营销/副业羊毛/抽奖
    AD_SPAM_B64 = [
        "44Od44Kk5rS7", "5LiN5Yqo5omA5b6X", "5pyI5Y+O", "5pel5omV44GE", "44OQ44Kk44OK44Oq44O8",
        "5oub5b6F44Kz44O844OJ", "VGlrVG9rIExpdGU=", "bGl0Lmxpbms=", "44OX44Os44K844Oz44OI5LyB55S7",
        "44Ki44Oe44Ku44OV", "UGF5UGF5", "5b2T6YG4", "5Y+X6Zqb", "5rWL6Lev", "5LyB55S7"
    ]
    # 成人商业交易/暗语变体
    NSFW_B64 = [
        "cOa0uw==", "cGo=", "dXJhYWth", "ZW5kZWxp", "44Kq44OV44OR44Kz", "44OH44Oq44OY44Or"
    ]

    @classmethod
    def decode_terms(cls, b64_list: List[str]) -> List[str]:
        terms = []
        for item in b64_list:
            try:
                terms.append(base64.b64decode(item).decode('utf-8'))
            except Exception:
                pass
        return terms

# 预编译红线正则
_FANDOM_TERMS = SafeVocabulary.decode_terms(SafeVocabulary.FANDOM_B64)
_AD_TERMS = SafeVocabulary.decode_terms(SafeVocabulary.AD_SPAM_B64)
_NSFW_TERMS = SafeVocabulary.decode_terms(SafeVocabulary.NSFW_B64)

_REDLINE_REGEX = re.compile(
    r'(?i)(' + '|'.join(re.escape(t) for t in (_FANDOM_TERMS + _AD_TERMS + _NSFW_TERMS) if t) + r')'
)

# 真实日本成年生活锚点词 (不可伪造的日常生活琐事与真实爱好)
ADULT_LIFE_ANCHORS = [
    # 餐饮生活
    "ラーメン", "カフェ", "ランチ", "晩酌", "自炊", "ビール", "定食", "美味しかった", "ごちそうさまでした", "お弁当",
    # 通勤与职场吐槽
    "出勤", "退勤", "残業", "お疲れ様", "雨", "晴れ", "暑い", "寒い", "電車遅延", "眠い", "帰宅", "有休",
    # 健康与休闲爱好
    "キャンプ", "バイク", "ツーリング", "散歩", "ドライブ", "筋トレ", "サウナ", "温泉", "読書", "釣り", "DIY"
]

class AuthenticityLearner:
    """老谷 AI 控制中心专属自学习闭环引擎"""

    @classmethod
    def init_db(cls):
        """初始化特征记忆库与 48 小时复核表"""
        conn = sqlite3.connect(LEARNING_DB_PATH, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        with conn:
            # 1. 画像特征记忆词库 (支持正向生活特征自强化)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS learned_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_word TEXT UNIQUE NOT NULL,
                weight INTEGER DEFAULT 1,
                hit_count INTEGER DEFAULT 1,
                last_reinforced_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feat_weight ON learned_features(weight DESC);")

            # 2. 互动记录与 48 小时回关复核跟踪表
            conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_tag TEXT NOT NULL,
                target_handle TEXT NOT NULL,
                action_type TEXT NOT NULL,          -- 'follow', 'like'
                status TEXT DEFAULT 'pending',      -- 'pending', 'followed_back', 'ignored'
                interacted_at DATETIME NOT NULL,
                check_after DATETIME NOT NULL,
                extracted_features TEXT,            -- JSON 字符串
                settled_at DATETIME
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status_check ON interaction_feedback(status, check_after);")

            # 预热默认生活锚点词
            for anchor in ADULT_LIFE_ANCHORS:
                conn.execute("""
                INSERT OR IGNORE INTO learned_features (feature_word, weight, hit_count)
                VALUES (?, 5, 1);
                """, (anchor,))
        conn.close()

    @classmethod
    def check_redlines(cls, text: str) -> Tuple[bool, str]:
        """0ms 本地红线检测"""
        if not text:
            return False, ""
        norm = unicodedata.normalize('NFKC', text)
        match = _REDLINE_REGEX.search(norm)
        if match:
            return True, f"命中垃圾/追星/营销红线特征: [{match.group(0)}]"
        return False, ""

    @classmethod
    def extract_life_features(cls, text: str) -> List[str]:
        """从推文或 Bio 中提取成年生活特征"""
        if not text:
            return []
        norm = unicodedata.normalize('NFKC', text)
        found = []
        for anchor in ADULT_LIFE_ANCHORS:
            if anchor in norm:
                found.append(anchor)
        return found

    @classmethod
    def evaluate_profile_authenticity(
        cls,
        handle: str,
        bio: str,
        tweets: List[str],
        follower_count: int,
        following_count: int
    ) -> Tuple[bool, int, str, List[str]]:
        """
        综合研判博主真人度
        返回: (是否通过, 综合评分 0-100, 原因, 提取到的生活特征列表)
        """
        cls.init_db()
        # 1. 红线排查
        all_text = f"{bio}\n" + "\n".join(tweets)
        hit, reason = cls.check_redlines(all_text)
        if hit:
            return False, 10, reason, []

        # 2. 互关比例核查 (散户通常互关比在 0.60 ~ 1.60)
        f_in = max(1, following_count)
        ratio = follower_count / f_in
        if follower_count > 40 and not (0.60 <= ratio <= 1.60):
            return False, 30, f"互关比异常 ({ratio:.2f})，不符合散户生活号特征", []

        # 3. 推文原创度核查
        total_tweets = len(tweets)
        original_tweets = sum(1 for t in tweets if not t.strip().startswith("RT @"))
        originality = (original_tweets / total_tweets) if total_tweets > 0 else 0.5
        if total_tweets >= 3 and originality < 0.34:
            return False, 25, f"推文原创度过低 ({originality:.0%})，疑似转发营销机器人", []

        # 4. 真实生活锚点匹配
        extracted = cls.extract_life_features(all_text)

        # 5. 读取动态学习库的权重加成
        db_bonus = 0
        if extracted:
            try:
                conn = sqlite3.connect(LEARNING_DB_PATH, timeout=5.0)
                placeholders = ','.join('?' * len(extracted))
                cur = conn.cursor()
                cur.execute(f"SELECT SUM(weight) FROM learned_features WHERE feature_word IN ({placeholders})", extracted)
                row = cur.fetchone()
                if row and row[0]:
                    db_bonus = min(25, int(row[0]))
                conn.close()
            except Exception:
                pass

        base_score = 50 + int(originality * 20) + min(20, len(extracted) * 10) + db_bonus
        final_score = max(0, min(100, base_score))

        if final_score < 60:
            return False, final_score, f"真人生活度评分不足 ({final_score} < 60)，缺乏可信生活细节", extracted

        anchors_desc = "、".join(extracted[:4]) if extracted else "自然日常发推"
        return True, final_score, f"真人认证通过 ({final_score}分)，生活锚点: [{anchors_desc}]", extracted

    @classmethod
    def record_interacted_candidate(
        cls,
        profile_tag: str,
        target_handle: str,
        action_type: str,
        features: List[str]
    ):
        """记录互动过的博主，进入 48 小时复核跟踪池"""
        cls.init_db()
        now = datetime.now(timezone.utc)
        check_after = (now + timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')
        clean_handle = target_handle.lstrip('@').strip().lower()

        try:
            conn = sqlite3.connect(LEARNING_DB_PATH, timeout=5.0)
            with conn:
                conn.execute("""
                INSERT INTO interaction_feedback (profile_tag, target_handle, action_type, status, interacted_at, check_after, extracted_features)
                VALUES (?, ?, ?, 'pending', ?, ?, ?);
                """, (profile_tag, clean_handle, action_type, now_str, check_after, json.dumps(features, ensure_ascii=False)))
            conn.close()
        except Exception as err:
            print(f"  └─ ⚠️ [自学习记录失败] {err}")

    @classmethod
    def check_and_reinforce_feedback(cls, check_followback_callback) -> int:
        """
        48小时回关结算与强化学习更新
        check_followback_callback: 接收 handle 返回 bool 的函数
        """
        cls.init_db()
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        try:
            conn = sqlite3.connect(LEARNING_DB_PATH, timeout=10.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
            SELECT id, target_handle, extracted_features FROM interaction_feedback
            WHERE status = 'pending' AND check_after <= ?
            LIMIT 20;
            """, (now_str,))
            pending_rows = cur.fetchall()

            if not pending_rows:
                conn.close()
                return 0

            settled_count = 0
            for row in pending_rows:
                r_id = row['id']
                handle = row['target_handle']
                features = json.loads(row['extracted_features'] or '[]')

                did_follow_back = check_followback_callback(handle)
                new_status = 'followed_back' if did_follow_back else 'ignored'

                conn.execute("""
                UPDATE interaction_feedback
                SET status = ?, settled_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """, (new_status, r_id))

                # 强化学习动态更新权重
                delta = 3 if did_follow_back else -1
                for f_word in features:
                    conn.execute("""
                    INSERT INTO learned_features (feature_word, weight, hit_count, last_reinforced_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(feature_word) DO UPDATE SET
                        weight = weight + ?,
                        hit_count = hit_count + 1,
                        last_reinforced_at = CURRENT_TIMESTAMP;
                    """, (f_word, max(1, 5 + delta), delta))

                settled_count += 1
                result_str = "[回关成功] (奖赏权重 +3)" if did_follow_back else "[未回关] (扣除权重 -1)"
                print(f"  └─ [强化学习结算] @{handle} -> {result_str}")

            conn.commit()
            conn.close()
            return settled_count
        except Exception as err:
            print(f"  └─ [强化学习结算异常] {err}")
            return 0

    @classmethod
    def get_top_learned_anchors(cls, limit: int = 20) -> List[Dict[str, Any]]:
        """读取置信度最高的生活锚点特征"""
        cls.init_db()
        try:
            conn = sqlite3.connect(LEARNING_DB_PATH, timeout=5.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT feature_word, weight, hit_count FROM learned_features ORDER BY weight DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    @classmethod
    def get_learning_stats(cls) -> Dict[str, Any]:
        """获取真人度自学习系统全局全景统计"""
        cls.init_db()
        stats = {
            "total_features_count": 0,
            "top_features": [],
            "queue_stats": {
                "pending_review_48h": 0,
                "followed_back_success": 0,
                "ignored_count": 0,
                "total_tracked": 0,
                "conversion_rate_pct": 0.0
            }
        }
        try:
            conn = sqlite3.connect(LEARNING_DB_PATH, timeout=5.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM learned_features;")
            stats["total_features_count"] = cur.fetchone()[0]

            stats["top_features"] = cls.get_top_learned_anchors(limit=10)

            cur.execute("""
            SELECT status, COUNT(*) as cnt
            FROM interaction_feedback
            GROUP BY status;
            """)
            status_map = {r[0]: r[1] for r in cur.fetchall()}
            pending = status_map.get('pending', 0)
            followed_back = status_map.get('followed_back', 0)
            ignored = status_map.get('ignored', 0)
            total = pending + followed_back + ignored
            rate = round((followed_back / (followed_back + ignored) * 100), 1) if (followed_back + ignored) > 0 else 0.0

            stats["queue_stats"] = {
                "pending_review_48h": pending,
                "followed_back_success": followed_back,
                "ignored_count": ignored,
                "total_tracked": total,
                "conversion_rate_pct": rate
            }
            conn.close()
        except Exception as e:
            stats["error"] = str(e)
        return stats

    @classmethod
    def extract_handle_from_input(cls, target_input: str) -> str:
        """从URL或用户输入中提取纯净 Twitter Handle"""
        if not target_input:
            return ""
        s = target_input.strip()
        # 匹配 URL: https://x.com/username or https://twitter.com/username
        m = re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})', s, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        # 匹配 @username 或 username
        m2 = re.search(r'@?([A-Za-z0-9_]{1,15})', s)
        if m2:
            return m2.group(1).lower()
        return s.lstrip('@').split('/')[0].strip().lower()

    @classmethod
    def learn_from_profile_input(
        cls,
        target_input: str,
        bio: str = "",
        tweets: Optional[List[str]] = None,
        follower_count: int = 150,
        following_count: int = 120,
        auto_reinforce_learning: bool = True
    ) -> Dict[str, Any]:
        """
        核心能力：接收博主主页链接或 ID，执行全要素真人度研判并自动沉淀生活特征自学习
        """
        handle = cls.extract_handle_from_input(target_input)
        if not handle:
            return {
                "success": False,
                "handle": "",
                "error": "无法解析有效博主 ID 或主页链接"
            }

        tweets_list = tweets if tweets is not None else []

        # 执行真人度深度研判
        is_authentic, score, reason, extracted_feats = cls.evaluate_profile_authenticity(
            handle=handle,
            bio=bio,
            tweets=tweets_list,
            follower_count=follower_count,
            following_count=following_count
        )

        learned_actions = []
        if is_authentic and auto_reinforce_learning and extracted_feats:
            # 自动将挖掘到的生活锚点词更新进动态知识库
            try:
                conn = sqlite3.connect(LEARNING_DB_PATH, timeout=5.0)
                with conn:
                    for f_word in extracted_feats:
                        cur = conn.cursor()
                        cur.execute("""
                        INSERT INTO learned_features (feature_word, weight, hit_count, last_reinforced_at)
                        VALUES (?, 5, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT(feature_word) DO UPDATE SET
                            hit_count = hit_count + 1,
                            last_reinforced_at = CURRENT_TIMESTAMP;
                        """, (f_word,))
                        learned_actions.append(f_word)
                conn.close()
            except Exception as e:
                learned_actions.append(f"DB Update Note: {e}")

        return {
            "success": True,
            "handle": handle,
            "is_authentic": is_authentic,
            "score": score,
            "reason": reason,
            "extracted_features": extracted_feats,
            "learned_features_updated": learned_actions,
            "reinforcement_policy": {
                "follow_back_reward": "+3 权重奖励",
                "unfollowed_penalty": "-1 权重微调",
                "threshold_score": 60
            }
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="老谷真人度自学习与特征强化引擎")
    parser.add_argument("--diagnose", type=str, help="诊断博主主页链接或ID的真人度")
    parser.add_argument("--stats", action="store_true", help="查看自学习记忆库全景统计")
    parser.add_argument("--reinforce", action="store_true", help="执行到期48小时回关强化学习复核")
    args = parser.parse_args()

    if args.stats:
        s = AuthenticityLearner.get_learning_stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif args.diagnose:
        res = AuthenticityLearner.learn_from_profile_input(
            args.diagnose,
            bio="都内在住のサラリーマン。夜はラーメンと自炊。休日はキャンプとサウナ。",
            tweets=["今日も残業お疲れ様でした。ビールがうまい！", "週末はふもとっぱらでキャンプ予定。"]
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        AuthenticityLearner.init_db()
        print("AuthenticityLearner initialized. DB:", LEARNING_DB_PATH)
        top = AuthenticityLearner.get_top_learned_anchors(limit=5)
        print("Top 5 anchors:", [(t['feature_word'], t['weight']) for t in top])

