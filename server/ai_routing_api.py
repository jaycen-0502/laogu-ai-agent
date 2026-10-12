# -*- coding: utf-8 -*-
"""
老谷控制中心 - 动态智能模型路由与 X 业务接口
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_routing_service import (
    RUNTIME_CONFIG,
    execute_llm_with_fallback,
    init_routing_defaults,
    ping_specific_model,
    update_runtime_config_db,
)
from .models import AIModelRegistry, AIRelayConfig, XAccountPersona, now

LOGGER = logging.getLogger("laogu.server.ai_routing_api")


class RelayConfigUpdate(BaseModel):
    base_url: str
    api_key: str
    default_model: str
    fallback_model: Optional[str] = "deepseek-chat"
    timeout: Optional[int] = 15


class ModelTestRequest(BaseModel):
    test_model_code: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ModelRegistryCreate(BaseModel):
    model_name: str
    model_code: str
    provider: Optional[str] = ""
    category: Optional[str] = ""
    status: Optional[int] = 1


class ModelRegistryUpdate(BaseModel):
    model_name: Optional[str] = None
    model_code: Optional[str] = None
    provider: Optional[str] = None
    category: Optional[str] = None
    status: Optional[int] = None


class XAccountPersonaDTO(BaseModel):
    account_id: str
    persona_name: Optional[str] = ""
    system_prompt: Optional[str] = ""
    custom_model: Optional[str] = ""  # 为空则继承全局
    temperature: Optional[float] = 0.75


class GenerateIcebreakerDTO(BaseModel):
    account_id: str
    persona_system_prompt: str
    target_tweet_content: str
    custom_model: Optional[str] = None
    temperature: Optional[float] = 0.75


class GenerateReplyDTO(BaseModel):
    account_id: str
    persona_system_prompt: str
    chat_history: list[dict[str, Any]]
    customer_latest_message: str
    custom_model: Optional[str] = None
    temperature: Optional[float] = 0.75


def register_ai_routing_routes(
    app: FastAPI,
    get_db: Callable[[], Session],
    current_user: Optional[Callable] = None,
) -> None:
    """注册动态模型路由与 X 业务端点"""

    # ================= 1. Web 后台：通道配置热更新与读取 =================
    @app.get("/admin/config")
    def get_relay_config(db: Session = Depends(get_db)):
        init_routing_defaults(db)
        return {
            "code": 200,
            "data": {
                "base_url": RUNTIME_CONFIG["base_url"],
                "api_key": RUNTIME_CONFIG["api_key"],
                "api_key_masked": f"****{RUNTIME_CONFIG['api_key'][-4:]}" if len(RUNTIME_CONFIG["api_key"]) >= 4 else "",
                "default_model": RUNTIME_CONFIG["default_model"],
                "fallback_model": RUNTIME_CONFIG["fallback_model"],
                "timeout": RUNTIME_CONFIG["timeout"],
            },
        }

    @app.post("/admin/config/update")
    def update_config(cfg: RelayConfigUpdate, db: Session = Depends(get_db)):
        """后台一键更改中转站、默认模型及版本"""
        try:
            saved = update_runtime_config_db(
                db=db,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                default_model=cfg.default_model,
                fallback_model=cfg.fallback_model or "deepseek-chat",
                timeout=cfg.timeout or 15,
            )
            return {
                "code": 200,
                "message": "配置与默认模型已热更新，即时生效！",
                "data": saved,
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"保存配置异常: {e}")

    # ================= 2. Web 后台：单模型即时测速/测活 =================
    @app.post("/admin/model/ping")
    def ping_model_endpoint(req: ModelTestRequest, db: Session = Depends(get_db)):
        """后台测试特定模型是否可用及响应耗时"""
        init_routing_defaults(db)
        try:
            result = ping_specific_model(
                test_model_code=req.test_model_code,
                base_url=req.base_url or RUNTIME_CONFIG["base_url"],
                api_key=req.api_key or RUNTIME_CONFIG["api_key"],
            )
            return result
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"模型 [{req.test_model_code}] 调用异常: {e}",
            )

    # ================= 3. Web 后台：模型字典管理 =================
    @app.get("/admin/models")
    def list_models(db: Session = Depends(get_db)):
        init_routing_defaults(db)
        items = db.execute(select(AIModelRegistry).order_by(AIModelRegistry.id.asc())).scalars().all()
        model_list = [
            {
                "id": m.id,
                "model_name": m.model_name,
                "model_code": m.model_code,
                "provider": m.provider,
                "category": m.category,
                "status": m.status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in items
        ]
        existing_codes = {m["model_code"] for m in model_list}
        try:
            from .models import AIProvider
            providers = db.execute(select(AIProvider).where(AIProvider.status == "ENABLED")).scalars().all()
            for prov in providers:
                prov_models = prov.available_models or []
                for p_model in prov_models:
                    p_code = str(p_model).strip()
                    if p_code and p_code not in existing_codes:
                        existing_codes.add(p_code)
                        model_list.append({
                            "id": None,
                            "model_name": f"{p_code} ({prov.name})",
                            "model_code": p_code,
                            "provider": prov.name,
                            "category": "服务商动态同步",
                            "status": 1,
                            "created_at": None,
                        })
        except Exception as err:
            LOGGER.debug("聚合 AIProvider 模型列表非阻断提示: %s", err)

        return {
            "code": 200,
            "data": model_list,
        }

    @app.post("/admin/models")
    def create_model(item: ModelRegistryCreate, db: Session = Depends(get_db)):
        existing = db.execute(
            select(AIModelRegistry).where(AIModelRegistry.model_code == item.model_code.strip())
        ).scalars().first()
        if existing:
            raise HTTPException(status_code=400, detail=f"模型标识 [{item.model_code}] 已存在")

        m = AIModelRegistry(
            model_name=item.model_name.strip(),
            model_code=item.model_code.strip(),
            provider=item.provider.strip() if item.provider else "",
            category=item.category.strip() if item.category else "",
            status=item.status if item.status is not None else 1,
            created_at=now(),
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        return {"code": 200, "message": "模型添加成功", "data": {"id": m.id, "model_code": m.model_code}}

    @app.put("/admin/models/{model_id}")
    def update_model(model_id: int, item: ModelRegistryUpdate, db: Session = Depends(get_db)):
        m = db.get(AIModelRegistry, model_id)
        if not m:
            raise HTTPException(status_code=404, detail="模型不存在")
        if item.model_name is not None:
            m.model_name = item.model_name.strip()
        if item.model_code is not None:
            m.model_code = item.model_code.strip()
        if item.provider is not None:
            m.provider = item.provider.strip()
        if item.category is not None:
            m.category = item.category.strip()
        if item.status is not None:
            m.status = item.status
        db.commit()
        return {"code": 200, "message": "模型更新成功"}

    @app.delete("/admin/models/{model_id}")
    def delete_model(model_id: int, db: Session = Depends(get_db)):
        m = db.get(AIModelRegistry, model_id)
        if not m:
            raise HTTPException(status_code=404, detail="模型不存在")
        db.delete(m)
        db.commit()
        return {"code": 200, "message": "模型删除成功"}

    # ================= 4. Web 后台：X 账号人设与模型独立绑定 =================
    @app.get("/api/x/personas")
    def list_personas(db: Session = Depends(get_db)):
        items = db.execute(select(XAccountPersona).order_by(XAccountPersona.id.asc())).scalars().all()
        return {
            "code": 200,
            "data": [
                {
                    "id": p.id,
                    "account_id": p.account_id,
                    "persona_name": p.persona_name,
                    "system_prompt": p.system_prompt,
                    "custom_model": p.custom_model,
                    "temperature": p.temperature,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }
                for p in items
            ],
        }

    @app.post("/api/x/personas")
    def upsert_persona(dto: XAccountPersonaDTO, db: Session = Depends(get_db)):
        p = db.execute(
            select(XAccountPersona).where(XAccountPersona.account_id == dto.account_id.strip())
        ).scalars().first()
        if not p:
            p = XAccountPersona(
                account_id=dto.account_id.strip(),
                persona_name=dto.persona_name or "",
                system_prompt=dto.system_prompt or "",
                custom_model=dto.custom_model or "",
                temperature=dto.temperature if dto.temperature is not None else 0.75,
                created_at=now(),
                updated_at=now(),
            )
            db.add(p)
        else:
            p.persona_name = dto.persona_name or ""
            p.system_prompt = dto.system_prompt or ""
            p.custom_model = dto.custom_model or ""
            p.temperature = dto.temperature if dto.temperature is not None else 0.75
            p.updated_at = now()
        db.commit()
        db.refresh(p)
        return {"code": 200, "message": "账号人设与模型绑定成功", "data": {"account_id": p.account_id}}

    @app.delete("/api/x/personas/{account_id}")
    def delete_persona(account_id: str, db: Session = Depends(get_db)):
        p = db.execute(
            select(XAccountPersona).where(XAccountPersona.account_id == account_id.strip())
        ).scalars().first()
        if not p:
            raise HTTPException(status_code=404, detail="人设不存在")
        db.delete(p)
        db.commit()
        return {"code": 200, "message": "人设删除成功"}

    # ================= 5. X 业务调用端点 =================
    @app.post("/api/x/generate-icebreaker")
    def generate_icebreaker(dto: GenerateIcebreakerDTO, db: Session = Depends(get_db)):
        """自动破冰：自动套用三级模型路由与人设"""
        init_routing_defaults(db)
        prompt = f"""【任务】：在 X(Twitter) 向潜在用户发起首次破冰打招呼私信。
【对方近期动态/简介】：{dto.target_tweet_content}
【要求】：
1. 态度温柔亲和，结合对方动态自然破冰，句尾带轻松小互动。
2. 纯正日本女性日常口吻，适当添加表情符号（1-2个即可）。
3. 仅输出最终私信文本。"""

        messages = [
            {"role": "system", "content": dto.persona_system_prompt or "你是一名温柔日本女性，善解人意。"},
            {"role": "user", "content": prompt},
        ]

        try:
            content, model_used = execute_llm_with_fallback(
                messages=messages,
                account_id=dto.account_id,
                preferred_model=dto.custom_model,
                scenario_model="gpt-4o-mini",
                temperature=dto.temperature or 0.75,
                max_tokens=260,
                db=db,
            )
            return {"code": 200, "text": content, "model_used": model_used}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/x/generate-reply")
    def generate_reply(dto: GenerateReplyDTO, db: Session = Depends(get_db)):
        """自动接话：自动套用三级模型路由与上下文"""
        init_routing_defaults(db)
        history_str = "\n".join(
            [f"{m.get('sender', 'user')}: {m.get('text', '')}" for m in (dto.chat_history or [])[-6:]]
        )

        prompt = f"""【任务】：高情商回复对方最新私信。
【聊天记录】：\n{history_str}
【对方最新消息】："{dto.customer_latest_message}"
【要求】：
1. 紧贴人设，态度温柔真诚，展现倾听与同理心。
2. 回复自然地道，仅输出日语回复内容。"""

        messages = [
            {"role": "system", "content": dto.persona_system_prompt or "你是一名温柔日本女性，善解人意。"},
            {"role": "user", "content": prompt},
        ]

        try:
            content, model_used = execute_llm_with_fallback(
                messages=messages,
                account_id=dto.account_id,
                preferred_model=dto.custom_model,
                scenario_model="deepseek-chat",
                temperature=dto.temperature or 0.75,
                max_tokens=260,
                db=db,
            )
            return {"code": 200, "text": content, "model_used": model_used}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
