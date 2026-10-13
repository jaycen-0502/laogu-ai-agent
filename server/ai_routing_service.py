# -*- coding: utf-8 -*-
"""
老谷控制中心 - 动态智能模型调度路由引擎服务
支持：
1. 运行时配置热加载 (RUNTIME_CONFIG) 与数据库持久化
2. 全局模型库字典管理与开机自动初始化推荐模型
3. 单模型即时测速/测活 (Ping)
4. 三级模型路由策略（账号专属 -> 场景专属 -> 全局默认）
5. 容灾自动降级兜底调用 (Fallback Recovery)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AIModelRegistry, AIRelayConfig, XAccountPersona, now

LOGGER = logging.getLogger("laogu.server.ai_routing")

# ================= 1. 运行时热加载配置 =================
RUNTIME_CONFIG: dict[str, Any] = {
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "default_model": "gpt-4o-mini",
    "fallback_model": "deepseek-chat",
    "timeout": 15,
}

DEFAULT_PRESET_MODELS = [
    {
        "model_name": "Claude 3.5 Sonnet (最新版)",
        "model_code": "claude-3-5-sonnet-20241022",
        "provider": "Anthropic",
        "category": "高价值深度聊客",
        "status": 1,
    },
    {
        "model_name": "GPT-4o Mini (经济快速)",
        "model_code": "gpt-4o-mini",
        "provider": "OpenAI",
        "category": "日常高频破冰",
        "status": 1,
    },
    {
        "model_name": "GPT-4o (旗舰全能)",
        "model_code": "gpt-4o",
        "provider": "OpenAI",
        "category": "复杂意图理解",
        "status": 1,
    },
    {
        "model_name": "DeepSeek V3 (Chat)",
        "model_code": "deepseek-chat",
        "provider": "DeepSeek",
        "category": "超高性价比接话",
        "status": 1,
    },
    {
        "model_name": "DeepSeek R1 (深度思考)",
        "model_code": "deepseek-reasoner",
        "provider": "DeepSeek",
        "category": "复杂逻辑研判",
        "status": 1,
    },
    {
        "model_name": "Claude 3.5 Haiku",
        "model_code": "claude-3-5-haiku-20241022",
        "provider": "Anthropic",
        "category": "极速日常打招呼",
        "status": 1,
    },
]


def normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return "https://api.openai.com/v1"
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def init_routing_defaults(db: Session) -> None:
    """初始化数据库默认配置与内置模型库字典"""
    try:
        # 1. 初始化通道配置
        relay_cfg = db.execute(select(AIRelayConfig).order_by(AIRelayConfig.id.asc())).scalars().first()
        if not relay_cfg:
            relay_cfg = AIRelayConfig(
                base_url=RUNTIME_CONFIG["base_url"],
                api_key=RUNTIME_CONFIG["api_key"],
                default_model=RUNTIME_CONFIG["default_model"],
                fallback_model=RUNTIME_CONFIG["fallback_model"],
                timeout=RUNTIME_CONFIG["timeout"],
                updated_at=now(),
            )
            db.add(relay_cfg)
            db.commit()
            db.refresh(relay_cfg)
        else:
            RUNTIME_CONFIG["base_url"] = normalize_base_url(relay_cfg.base_url)
            RUNTIME_CONFIG["api_key"] = relay_cfg.api_key or ""
            RUNTIME_CONFIG["default_model"] = relay_cfg.default_model or "gpt-4o-mini"
            RUNTIME_CONFIG["fallback_model"] = relay_cfg.fallback_model or "deepseek-chat"
            RUNTIME_CONFIG["timeout"] = relay_cfg.timeout or 15

        # 2. 初始化模型库预设
        existing_codes = set(db.execute(select(AIModelRegistry.model_code)).scalars().all())
        added = False
        for preset in DEFAULT_PRESET_MODELS:
            if preset["model_code"] not in existing_codes:
                item = AIModelRegistry(
                    model_name=preset["model_name"],
                    model_code=preset["model_code"],
                    provider=preset["provider"],
                    category=preset["category"],
                    status=preset["status"],
                    created_at=now(),
                )
                db.add(item)
                added = True
        if added:
            db.commit()
    except Exception as e:
        db.rollback()
        LOGGER.warning("初始化模型路由与字典异常: %s", e)


def update_runtime_config_db(
    db: Session,
    base_url: str,
    api_key: str,
    default_model: str,
    fallback_model: str = "deepseek-chat",
    timeout: int = 15,
) -> dict[str, Any]:
    """热更新全局通道配置并同步持久化至数据库"""
    clean_url = normalize_base_url(base_url)
    relay_cfg = db.execute(select(AIRelayConfig).order_by(AIRelayConfig.id.asc())).scalars().first()
    if not relay_cfg:
        relay_cfg = AIRelayConfig(
            base_url=clean_url,
            api_key=api_key,
            default_model=default_model,
            fallback_model=fallback_model or "deepseek-chat",
            timeout=timeout,
            updated_at=now(),
        )
        db.add(relay_cfg)
    else:
        relay_cfg.base_url = clean_url
        relay_cfg.api_key = api_key
        relay_cfg.default_model = default_model
        relay_cfg.fallback_model = fallback_model or "deepseek-chat"
        relay_cfg.timeout = timeout
        relay_cfg.updated_at = now()

    db.commit()

    # 热更新内存
    RUNTIME_CONFIG["base_url"] = clean_url
    RUNTIME_CONFIG["api_key"] = api_key
    RUNTIME_CONFIG["default_model"] = default_model
    RUNTIME_CONFIG["fallback_model"] = fallback_model or "deepseek-chat"
    RUNTIME_CONFIG["timeout"] = timeout
    return dict(RUNTIME_CONFIG)


DEFAULT_RELAY_HEADERS = {
    "Originator": "codex_cli_rs",
    "User-Agent": "codex_cli_rs/0.101.0 (Mac OS; arm64)",
    "Version": "0.101.0",
}


def guess_model_provider(model_code: str) -> str:
    """根据模型代码推测提供商名称"""
    code = (model_code or "").lower()
    if "claude" in code:
        return "Anthropic"
    if "gpt" in code or "o1" in code or "o3" in code or "dall-e" in code:
        return "OpenAI"
    if "deepseek" in code:
        return "DeepSeek"
    if "gemini" in code:
        return "Google"
    if "qwen" in code:
        return "Qwen"
    if "glm" in code or "chatglm" in code:
        return "Zhipu"
    if "llama" in code:
        return "Meta"
    if "mistral" in code or "mixtral" in code:
        return "Mistral"
    return "Custom"


def fetch_remote_relay_models(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = 12,
) -> list[dict[str, Any]]:
    """向中转站发送请求，自动探测真实可用模型列表（兼容 CC Switch 自动拉取逻辑）"""
    clean_url = normalize_base_url(base_url or RUNTIME_CONFIG["base_url"])
    key = api_key or RUNTIME_CONFIG["api_key"]
    if not key:
        raise ValueError("请先输入中转站 API Key 再进行模型检测")

    endpoint = f"{clean_url}/models"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **DEFAULT_RELAY_HEADERS,
    }

    with httpx.Client(timeout=float(timeout)) as client:
        resp = client.get(endpoint, headers=headers)
        if resp.status_code != 200:
            err_msg = resp.text[:300]
            raise RuntimeError(f"中转站返回 HTTP {resp.status_code}: {err_msg}")
        payload = resp.json()

    raw_items = []
    if isinstance(payload, dict):
        raw_items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        raw_items = payload

    models: list[dict[str, Any]] = []
    seen = set()
    for item in raw_items:
        m_id = ""
        if isinstance(item, dict):
            m_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
        elif isinstance(item, str):
            m_id = item.strip()

        if m_id and m_id not in seen:
            seen.add(m_id)
            models.append({
                "model_code": m_id,
                "model_name": m_id,
                "provider": guess_model_provider(m_id),
                "category": "中转站自动检测",
                "status": 1,
            })

    if not models:
        raise RuntimeError("未能从中转站获取到有效模型列表，请确认 API Key 权限或中转站服务状态")

    return models


def call_openai_chat_completions(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.75,
    max_tokens: int = 260,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """标准 OpenAI 兼容协议客户端调用"""
    endpoint = f"{normalize_base_url(base_url or RUNTIME_CONFIG['base_url'])}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key or RUNTIME_CONFIG['api_key']}",
        **DEFAULT_RELAY_HEADERS,
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req_timeout = timeout or RUNTIME_CONFIG.get("timeout", 15)

    with httpx.Client(timeout=float(req_timeout)) as client:
        resp = client.post(endpoint, headers=headers, json=payload)
        if resp.status_code != 200:
            err_msg = resp.text[:300]
            raise RuntimeError(f"HTTP {resp.status_code}: {err_msg}")
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"返回结果无 choices: {resp.text[:200]}")
        content = choices[0].get("message", {}).get("content") or ""
        return content.strip()


def ping_specific_model(
    test_model_code: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[int] = 10,
) -> dict[str, Any]:
    """后台测试特定模型是否可用及响应耗时"""
    start_time = time.time()
    try:
        content = call_openai_chat_completions(
            model=test_model_code,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            temperature=0.7,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout or 10,
        )
        latency = round((time.time() - start_time) * 1000, 2)
        return {
            "code": 200,
            "status": "online",
            "model_tested": test_model_code,
            "latency": f"{latency}ms",
            "sample_response": content,
        }
    except Exception as e:
        latency = round((time.time() - start_time) * 1000, 2)
        raise RuntimeError(f"[{test_model_code}] ({latency}ms) 调用异常: {e}")


def execute_llm_with_fallback(
    messages: list[dict[str, Any]],
    account_id: Optional[str] = None,
    preferred_model: Optional[str] = None,
    scenario_model: Optional[str] = None,
    temperature: float = 0.75,
    max_tokens: int = 260,
    db: Optional[Session] = None,
) -> tuple[str, str]:
    """
    智能三级模型路由与容灾降级调用器
    优先级决策顺序：
    1. 账号专属绑定模型 (custom_model from x_account_personas)
    2. 请求传入/场景指定模型 (preferred_model / scenario_model)
    3. 全局默认模型 (RUNTIME_CONFIG["default_model"])

    容灾降级：
    主调模型若遇超时或报错，自动秒级切换至备用模型 (RUNTIME_CONFIG["fallback_model"])。
    返回: (生成文本, 最终使用的模型名称)
    """
    primary_model = ""
    target_temperature = temperature

    # 1. 优先级一：账号专属配置
    if account_id and db:
        try:
            persona = db.execute(
                select(XAccountPersona).where(XAccountPersona.account_id == str(account_id))
            ).scalars().first()
            if persona:
                if persona.custom_model and persona.custom_model.strip():
                    primary_model = persona.custom_model.strip()
                if persona.temperature is not None and persona.temperature > 0:
                    target_temperature = persona.temperature
        except Exception as e:
            LOGGER.debug("读取账号专属人设模型异常: %s", e)

    # 2. 优先级二：场景专属模型
    if not primary_model and preferred_model:
        primary_model = preferred_model.strip()
    if not primary_model and scenario_model:
        primary_model = scenario_model.strip()

    # 3. 优先级三：全局默认模型
    if not primary_model:
        primary_model = RUNTIME_CONFIG.get("default_model") or "gpt-4o-mini"

    # --- 执行首选模型 ---
    try:
        content = call_openai_chat_completions(
            model=primary_model,
            messages=messages,
            temperature=target_temperature,
            max_tokens=max_tokens,
        )
        return content, primary_model
    except Exception as primary_err:
        LOGGER.warning("首选模型 [%s] 调用失败: %s，启动容灾降级...", primary_model, primary_err)

        # --- 容灾降级：切换至备用模型 ---
        fallback_model = RUNTIME_CONFIG.get("fallback_model") or "deepseek-chat"
        if fallback_model == primary_model:
            fallback_model = "gpt-4o-mini" if primary_model != "gpt-4o-mini" else "deepseek-chat"

        try:
            content = call_openai_chat_completions(
                model=fallback_model,
                messages=messages,
                temperature=target_temperature,
                max_tokens=max_tokens,
            )
            return content, f"{fallback_model} (降级生效)"
        except Exception as fallback_err:
            LOGGER.error("备用降级模型 [%s] 亦调用失败: %s", fallback_model, fallback_err)
            raise RuntimeError(
                f"主模型 [{primary_model}] 与备用模型 [{fallback_model}] 均调用失败: {fallback_err}"
            )
