# -*- coding: utf-8 -*-
"""
Tests for dynamic AI model routing, registry, personas, and fallback execution.
"""
from fastapi.testclient import TestClient
import pytest
from unittest.mock import patch

from server.main import app, create_app
from server.database import Base, create_database
from server.models import AIModelRegistry, AIRelayConfig, XAccountPersona


@pytest.fixture
def client():
    test_app = create_app()
    return TestClient(test_app)


def test_ai_routing_config_and_models(client):
    # 1. Test GET /admin/config
    resp = client.get("/admin/config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "base_url" in data
    assert "default_model" in data
    assert "fallback_model" in data

    # 2. Test POST /admin/config/update
    update_payload = {
        "base_url": "https://api.test-relay.com/v1",
        "api_key": "sk-test-secret-key-1234",
        "default_model": "gpt-4o-mini",
        "fallback_model": "deepseek-chat",
        "timeout": 20,
    }
    resp = client.post("/admin/config/update", json=update_payload)
    assert resp.status_code == 200
    assert resp.json()["code"] == 200

    # Verify update persisted
    resp = client.get("/admin/config")
    assert resp.json()["data"]["base_url"] == "https://api.test-relay.com/v1"
    assert resp.json()["data"]["timeout"] == 20

    # 3. Test GET /admin/models
    resp = client.get("/admin/models")
    assert resp.status_code == 200
    models = resp.json()["data"]
    assert len(models) >= 5
    codes = [m["model_code"] for m in models]
    assert "claude-3-5-sonnet-20241022" in codes
    assert "gpt-4o-mini" in codes

    # 4. Test POST /admin/models (Add new model)
    new_model = {
        "model_name": "Claude 3.7 Sonnet (新旗舰)",
        "model_code": "claude-3-7-sonnet-preview",
        "provider": "Anthropic",
        "category": "深度推理与大客户聊客",
        "status": 1,
    }
    resp = client.post("/admin/models", json=new_model)
    assert resp.status_code == 200
    model_id = resp.json()["data"]["id"]

    # 5. Test PUT /admin/models/{id}
    resp = client.put(f"/admin/models/{model_id}", json={"category": "VIP专属转化"})
    assert resp.status_code == 200

    # 6. Test DELETE /admin/models/{id}
    resp = client.delete(f"/admin/models/{model_id}")
    assert resp.status_code == 200


def test_x_account_personas_crud(client):
    # 1. Upsert persona
    persona_payload = {
        "account_id": "acc_test_golf_01",
        "persona_name": "由纪 (Yuki)",
        "system_prompt": "你是一名温柔日本高尔夫女教练，口吻真诚亲切。",
        "custom_model": "claude-3-5-sonnet-20241022",
        "temperature": 0.8,
    }
    resp = client.post("/api/x/personas", json=persona_payload)
    assert resp.status_code == 200

    # 2. List personas
    resp = client.get("/api/x/personas")
    assert resp.status_code == 200
    personas = resp.json()["data"]
    target = next((p for p in personas if p["account_id"] == "acc_test_golf_01"), None)
    assert target is not None
    assert target["persona_name"] == "由纪 (Yuki)"
    assert target["custom_model"] == "claude-3-5-sonnet-20241022"
    assert target["temperature"] == 0.8

    # 3. Delete persona
    resp = client.delete("/api/x/personas/acc_test_golf_01")
    assert resp.status_code == 200


def test_icebreaker_and_reply_routing_with_fallback(client):
    # Mock call_openai_chat_completions to test three-tier routing & fallback recovery
    call_log = []

    def mock_chat_completions(model, messages, temperature=0.75, max_tokens=260, **kwargs):
        call_log.append(model)
        if model == "broken-model":
            raise RuntimeError("API Gateway Connection Timeout 504")
        return f"こんにちは！私は{model}で生成しました。"

    with patch("server.ai_routing_service.call_openai_chat_completions", side_effect=mock_chat_completions):
        # 1. Icebreaker using custom model
        ice_dto = {
            "account_id": "acc_01",
            "persona_system_prompt": "温柔女性人设",
            "target_tweet_content": "最近ゴルフの練習を始めました！",
            "custom_model": "claude-3-5-sonnet-20241022",
            "temperature": 0.75,
        }
        resp = client.post("/api/x/generate-icebreaker", json=ice_dto)
        assert resp.status_code == 200
        data = resp.json()
        assert "claude-3-5-sonnet-20241022" in data["model_used"]
        assert "こんにちは" in data["text"]

        # 2. Test Fallback recovery when primary model is broken
        call_log.clear()
        broken_dto = {
            "account_id": "acc_02",
            "persona_system_prompt": "温柔女性人设",
            "target_tweet_content": "愛車とドライブ中！",
            "custom_model": "broken-model",
            "temperature": 0.75,
        }
        resp = client.post("/api/x/generate-icebreaker", json=broken_dto)
        assert resp.status_code == 200
        data = resp.json()
        # Primary failed, fallback succeeded
        assert "broken-model" in call_log
        assert "deepseek-chat" in call_log
        assert "降级生效" in data["model_used"]

        # 3. Test generate reply
        call_log.clear()
        reply_dto = {
            "account_id": "acc_03",
            "persona_system_prompt": "温柔女性人设",
            "chat_history": [
                {"sender": "customer", "text": "はじめまして！"},
                {"sender": "me", "text": "はじめまして、よろしくお願いします！"},
            ],
            "customer_latest_message": "普段はどんなお仕事されていますか？",
            "custom_model": "gpt-4o-mini",
            "temperature": 0.75,
        }
        resp = client.post("/api/x/generate-reply", json=reply_dto)
        assert resp.status_code == 200
        assert "gpt-4o-mini" in resp.json()["model_used"]
