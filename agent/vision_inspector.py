from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent.vision")

# 内存级视觉去重缓存（记录已审视过的图片 URL 或博主 Handle，防重复开销）
_VISION_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_MAX_CACHE_SIZE = 2000


class VisionInspector:
    """真人级多模态视觉审视引擎：
    1. 头像视觉分类：识别真实车辆/机车/真实真人，过滤营销广告图、二次元动漫引流号、低质图；
    2. 推文配图扫毒：识别推文配图中隐藏的宣传海报、二维码、LINE/微信引流暗号、副业招募；
    3. 全流程异步超时隔离与内存 LRU 缓存，保证毫秒级流转，绝不卡死自动化主流程。
    """

    def __init__(self, enable_network_vision: bool = True):
        self.enable_network_vision = enable_network_vision

    def _get_api_credentials(self) -> Tuple[str, str, str]:
        """静默获取系统内置的路由凭据，无需用户进行任何复杂配置"""
        key = os.environ.get("ROUTER_API_KEY", "")
        url = os.environ.get("ROUTER_BASE_URL", "")
        model = "gpt-4o-mini"

        if not url:
            url = "https://api.openai.com/v1"
        elif not url.startswith("http"):
            url = f"https://{url}"

        # 补全 chat completions 路径
        endpoint = url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            if endpoint.endswith("/v1"):
                endpoint = f"{endpoint}/chat/completions"
            else:
                endpoint = f"{endpoint}/v1/chat/completions"

        return key, endpoint, model

    @staticmethod
    def _is_cached(cache_key: str) -> Optional[Tuple[bool, str]]:
        with _CACHE_LOCK:
            cached = _VISION_CACHE.get(cache_key)
            if cached:
                # 缓存有效期 6 小时
                if time.time() - cached.get("ts", 0) < 21600:
                    return cached.get("is_valid", True), cached.get("reason", "来自视觉缓存")
        return None

    @staticmethod
    def _save_cache(cache_key: str, is_valid: bool, reason: str) -> None:
        global _VISION_CACHE
        with _CACHE_LOCK:
            if len(_VISION_CACHE) > _MAX_CACHE_SIZE:
                # 剔除最早的 500 条
                keys = list(_VISION_CACHE.keys())[:500]
                for k in keys:
                    _VISION_CACHE.pop(k, None)
            _VISION_CACHE[cache_key] = {
                "is_valid": is_valid,
                "reason": reason,
                "ts": time.time()
            }

    @staticmethod
    def inspect_local_heuristics(
        avatar_url: str = "",
        tweet_image_urls: Optional[List[str]] = None,
        image_alts: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """【第 1 层：端侧零耗时启发式视觉初筛】(无需网络，毫秒级响应)
        返回值: (通过/不通过, 原因描述)
        """
        # 1. 头像如果是默认蛋头、系统剪影，直接一票否决
        av_low = avatar_url.lower()
        if "default_profile" in av_low or "silhouette" in av_low or "egg" in av_low:
            return False, "默认初始头像 (零个性特征，疑似水军/弃号)"

        # 2. 检查图片 Alt 描述信息（推特官方自动无障碍识别的图片实体标签）
        if image_alts:
            ad_poster_keywords = [
                "poster", "flyer", "advertisement", "ad", "banner", "billboard",
                "qr code", "qrcode", "coupon", "brochure", "pamphlet",
                "海報", "チラシ", "広告", "宣伝", "ポスター", "バナー", "クーポン", "販促", "告知"
            ]
            for alt in image_alts:
                alt_low = alt.lower()
                # 若推特 Alt 识别出明显的广告/海报/文字特征
                if any(k in alt_low for k in ad_poster_keywords):
                    return False, f"推特视觉识别检测到广告海报实体: '{alt}'"
                # 若推特 Alt 直接识别出了车辆相关实体，给予高分通过
                if any(k in alt_low for k in ["car", "vehicle", "automobile", "motorcycle", "bike", "sedan", "coupe", "suv", "truck", "車", "自動車", "バイク", "愛車", "単車"]):
                    return True, f"推特原生视觉命中车辆实体: '{alt}'"

        return True, "本地启发式初筛通过"

    def inspect_multimodal(
        self,
        image_url: str,
        context_type: str = "avatar",
        handle: str = ""
    ) -> Tuple[bool, str]:
        """【第 2 层：云端多模态大模型真人级审视】
        调用 OpenAI / Gemini / 兼容端点的大模型视觉接口。
        """
        key, endpoint, model = self._get_api_credentials()
        if not key or key.startswith("sk-你的") or key.startswith("sk-placeholder"):
            return True, "未配置有效视觉接口密钥，自动放行保底"

        prompt = (
            f"你是一个推特真实汽车爱好者识别引擎。请观察这张博主 @{handle} 的推特图片（类型: {context_type}）。\n"
            "任务：判断其是否为日本真实的个人汽车/摩托车车主，或者是营销号/商业车贩/二次元引流/色情广告。\n\n"
            "【判定准则】\n"
            "1. 准入通过 (true)：图片包含真实汽车、摩托车实拍、仪表盘、改装件、自驾风景、车库、或真人自然生活。\n"
            "2. 淘汰拦截 (false)：图片包含宣传海报、二维码、兼职副业招募、带文字的广告单、商业车商广告招牌、暴露色情诱导图、纯二次元动漫引流。\n"
            "3. 无法完全看清时，保持宽容放行。\n\n"
            "请严格输出 JSON: {\"is_target\": true, \"reason\": \"简明扼要原因 (15字内)\"}"
        )

        try:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}}
                        ]
                    }
                ],
                "max_tokens": 80,
                "temperature": 0.1
            }

            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VisionAgent/2026.1"
                },
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    choices = resp_data.get("choices", [])
                    if choices:
                        raw_content = choices[0].get("message", {}).get("content", "").strip()
                        # 清理 markdown 代码块标签
                        clean_content = re.sub(r"```(?:json)?", "", raw_content).strip("` \n")
                        data = json.loads(clean_content)
                        is_target = bool(data.get("is_target", True))
                        reason = str(data.get("reason", "视觉审视完成")).strip()
                        return is_target, reason
        except Exception as exc:
            logger.debug("视觉接口审视偶发异常 (降级放行): %s", exc)
            return True, f"视觉接口微抖动降级放行 ({exc})"

        return True, "视觉接口默认放行"

    def inspect_candidate_sync(
        self,
        handle: str,
        avatar_url: str = "",
        tweet_image_urls: Optional[List[str]] = None,
        image_alts: Optional[List[str]] = None,
        enable_ai_vision: bool = False
    ) -> Tuple[bool, str]:
        """同步检查接口，支持在 asyncio.to_thread 中运行"""
        cache_key = f"{handle.lower()}:{avatar_url[:80]}"
        cached_result = self._is_cached(cache_key)
        if cached_result is not None:
            return cached_result

        # 1. 本地初筛
        local_ok, local_reason = self.inspect_local_heuristics(avatar_url, tweet_image_urls, image_alts)
        if not local_ok:
            self._save_cache(cache_key, False, local_reason)
            return False, local_reason

        # 2. 若开启了 AI 多模态视觉审视且有头像或配图
        if enable_ai_vision and self.enable_network_vision:
            target_img = avatar_url or (tweet_image_urls[0] if tweet_image_urls else "")
            if target_img:
                ai_ok, ai_reason = self.inspect_multimodal(target_img, context_type="avatar", handle=handle)
                self._save_cache(cache_key, ai_ok, ai_reason)
                return ai_ok, ai_reason

        self._save_cache(cache_key, True, "视觉审视通过")
        return True, "视觉审视通过"
