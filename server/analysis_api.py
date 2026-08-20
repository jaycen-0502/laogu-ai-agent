from __future__ import annotations

from collections import Counter
from datetime import timedelta
import json
import time
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .ai_provider import CredentialCipher, CredentialError
from .ai_service import AIRequestError, AIRequestTimeout
from .ai_policy import resolve_provider
from .analysis_service import AIAnalysisRunResult, account_messages, keyword_messages, sanitize_analysis_text
from .models import Account, Activity, AIAnalysis, AIProvider, User, now
from .schemas import AIAccountAnalysisCreate, AIKeywordAnalysisCreate
from .security import audit


ANALYSIS_TYPES = frozenset({"ACCOUNT", "KEYWORD"})


def _dt(value):
    return value.isoformat() if value else None


def _analysis_dict(item: AIAnalysis, provider_name: str = "", *, detail: bool = False) -> dict:
    payload = {
        "analysis_id": item.id,
        "workspace_id": item.workspace_id,
        "user_id": item.user_id,
        "provider_id": item.provider_id,
        "provider_name": provider_name,
        "account_id": item.account_id,
        "analysis_type": item.analysis_type,
        "title": item.title,
        "model": item.model,
        "status": item.status,
        "keywords": item.keywords or [],
        "summary": item.summary,
        "prompt_tokens": item.prompt_tokens,
        "completion_tokens": item.completion_tokens,
        "total_tokens": item.total_tokens,
        "latency_ms": item.latency_ms,
        "error_code": item.error_code,
        "error": item.error,
        "created_at": _dt(item.created_at),
        "completed_at": _dt(item.completed_at),
    }
    if detail:
        payload.update({
            "input_text": item.input_text,
            "source_snapshot": item.source_snapshot or {},
            "result": item.result or {},
        })
    return payload


def _activity_text(items: list[Activity]) -> str:
    parts: list[str] = []
    for item in items[:100]:
        parts.append(item.summary or "")
        if item.result:
            parts.append(json.dumps(item.result, ensure_ascii=False, default=str))
        if item.logs:
            parts.append(json.dumps(item.logs, ensure_ascii=False, default=str))
    return sanitize_analysis_text("\n".join(part for part in parts if part))[:20000]


def _account_snapshot(account: Account, activities: list[Activity], lookback_days: int) -> dict:
    statuses = Counter(item.status for item in activities)
    successes = statuses.get("SUCCESS", 0)
    durations = [max(0.0, float(item.duration or 0)) for item in activities]
    return {
        "account": {
            "account_id": account.id,
            "profile_id": account.profile_id,
            "x_username": account.x_username,
            "x_account_id": account.x_account_id,
            "login_status": account.login_status,
            "browser_status": account.browser_status,
            "account_status": account.account_status,
            "last_checked": _dt(account.last_checked),
        },
        "lookback_days": lookback_days,
        "activity_metrics": {
            "sample_size": len(activities),
            "status_counts": dict(statuses),
            "success_rate": round(successes / len(activities), 4) if activities else 0,
            "average_duration_seconds": round(sum(durations) / len(durations), 3) if durations else 0,
            "last_activity_at": _dt(activities[0].timestamp) if activities else None,
        },
        "data_quality": {
            "sample_sufficient": len(activities) >= 10,
            "limitations": [
                "当前数据是账号运行状态和自动化活动，不包含X帖子、粉丝、曝光或互动指标。",
                *(["活动样本少于10条，只能作为运行健康参考。"] if len(activities) < 10 else []),
            ],
        },
    }


def _normalized_keywords(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        key = item.casefold()
        if not item or len(item) > 80 or key in seen:
            continue
        seen.add(key)
        result.append(item)
    if not result:
        raise HTTPException(status_code=422, detail="At least one valid keyword is required")
    return result


def register_analysis_routes(
    app: FastAPI,
    *,
    get_db: Callable,
    current_user: Callable,
    paged: Callable,
    cipher: CredentialCipher,
    analysis_service,
) -> None:
    app.state.ai_analysis_service = analysis_service

    def visible_account(account_id: str, user: User, db: Session) -> Account:
        item = db.get(Account, account_id)
        if not item or (user.role != "ADMIN" and item.workspace_id != user.workspace_id):
            raise HTTPException(status_code=404, detail="Account not found")
        return item

    def visible_analysis(analysis_id: str, user: User, db: Session) -> AIAnalysis:
        item = db.get(AIAnalysis, analysis_id)
        if not item or item.user_id != user.id:
            raise HTTPException(status_code=404, detail="AI analysis not found")
        return item

    def checked_provider(
        db: Session,
        user: User,
        workspace_id: str,
        provider_id: str | None,
        model: str | None,
    ) -> tuple[AIProvider, str]:
        return resolve_provider(db, user, "ANALYSIS", provider_id, model, workspace_id=workspace_id)

    def account_activities(db: Session, account: Account, lookback_days: int) -> list[Activity]:
        since = now() - timedelta(days=lookback_days)
        identity = [Activity.profile_id == account.profile_id]
        if account.x_account_id:
            identity.append(Activity.x_account_id == account.x_account_id)
        query = (
            select(Activity)
            .where(
                Activity.workspace_id == account.workspace_id,
                or_(*identity),
                Activity.timestamp >= since,
            )
            .order_by(Activity.timestamp.desc())
            .limit(100)
        )
        return list(db.scalars(query))

    def execute(item: AIAnalysis, provider: AIProvider, messages: list[dict[str, str]], request: Request, user: User, db: Session) -> dict:
        started = time.monotonic()
        action = "AI_ANALYSIS_FAILED"
        http_error: HTTPException | None = None
        try:
            api_key = cipher.decrypt(provider.api_key_encrypted)
            output: AIAnalysisRunResult = app.state.ai_analysis_service.run(
                base_url=provider.base_url,
                api_key=api_key,
                model=item.model,
                messages=messages,
            )
            item.status = "SUCCESS"
            item.result = output.result
            item.summary = output.summary
            item.prompt_tokens = output.usage.prompt_tokens
            item.completion_tokens = output.usage.completion_tokens
            item.total_tokens = output.usage.total_tokens
            action = "AI_ACCOUNT_ANALYZED" if item.analysis_type == "ACCOUNT" else "AI_KEYWORDS_ANALYZED"
        except CredentialError:
            item.status = "FAILED"
            item.error_code = "CREDENTIAL_ERROR"
            item.error = "AI凭据不可用"
            http_error = HTTPException(status_code=503, detail=item.error)
        except AIRequestTimeout:
            item.status = "FAILED"
            item.error_code = "TIMEOUT"
            item.error = "AI分析请求超时"
            http_error = HTTPException(status_code=504, detail=item.error)
        except AIRequestError:
            item.status = "FAILED"
            item.error_code = "PROVIDER_ERROR"
            item.error = "AI分析服务请求失败"
            http_error = HTTPException(status_code=502, detail=item.error)
        except Exception:
            item.status = "FAILED"
            item.error_code = "INTERNAL_ERROR"
            item.error = "AI分析服务暂时不可用"
            http_error = HTTPException(status_code=502, detail=item.error)
        item.latency_ms = max(0, round((time.monotonic() - started) * 1000))
        item.completed_at = now()
        db.commit()
        audit(
            db,
            request,
            action=action,
            result=item.status,
            user_id=user.id,
            workspace_id=item.workspace_id,
            profile_id=(db.get(Account, item.account_id).profile_id if item.account_id and db.get(Account, item.account_id) else None),
            resource_type="ai_analysis",
            resource_id=item.id,
            message=item.error,
        )
        if http_error:
            raise http_error
        return _analysis_dict(item, provider.name, detail=True)

    @app.get("/api/ai/analysis")
    def list_analyses(
        analysis_type: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        query = select(AIAnalysis).where(AIAnalysis.user_id == user.id)
        if analysis_type.strip():
            normalized = analysis_type.strip().upper()
            if normalized not in ANALYSIS_TYPES:
                raise HTTPException(status_code=422, detail="Invalid analysis type")
            query = query.where(AIAnalysis.analysis_type == normalized)
        query = query.order_by(AIAnalysis.created_at.desc())

        def serialize(item: AIAnalysis) -> dict:
            provider = db.get(AIProvider, item.provider_id)
            return _analysis_dict(item, provider.name if provider else "")

        return paged(db, query, serialize, page=page, page_size=page_size)

    @app.get("/api/ai/analysis/{analysis_id}")
    def get_analysis(analysis_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        item = visible_analysis(analysis_id, user, db)
        provider = db.get(AIProvider, item.provider_id)
        return _analysis_dict(item, provider.name if provider else "", detail=True)

    @app.post("/api/ai/analysis/account")
    def analyze_account(
        body: AIAccountAnalysisCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        account = visible_account(body.account_id, user, db)
        provider, model = checked_provider(db, user, account.workspace_id, body.provider_id, body.model)
        activities = account_activities(db, account, body.lookback_days)
        snapshot = _account_snapshot(account, activities, body.lookback_days)
        name = account.x_username or account.profile_id
        item = AIAnalysis(
            workspace_id=account.workspace_id,
            user_id=user.id,
            provider_id=provider.id,
            account_id=account.id,
            analysis_type="ACCOUNT",
            title=f"账号分析：{name}"[:160],
            model=model,
            status="PENDING",
            keywords=[],
            input_text="",
            source_snapshot=snapshot,
            result={},
            summary="",
            created_at=now(),
        )
        db.add(item)
        db.commit()
        return execute(item, provider, account_messages(snapshot), request, user, db)

    @app.post("/api/ai/analysis/keywords")
    def analyze_keywords(
        body: AIKeywordAnalysisCreate,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        keywords = _normalized_keywords(body.keywords)
        account = visible_account(body.account_id, user, db) if body.account_id else None
        workspace_id = account.workspace_id if account else user.workspace_id
        if not account and body.provider_id and user.role == "ADMIN":
            selected_provider = db.get(AIProvider, body.provider_id)
            if selected_provider:
                workspace_id = selected_provider.workspace_id
        if not workspace_id:
            raise HTTPException(status_code=422, detail="Workspace is required")
        provider, model = checked_provider(db, user, workspace_id, body.provider_id, body.model)
        activities = account_activities(db, account, body.lookback_days) if account else []
        source_parts = [sanitize_analysis_text(body.input_text)]
        activity_source = _activity_text(activities)
        if activity_source:
            source_parts.append(activity_source)
        source_text = "\n".join(part for part in source_parts if part).strip()[:20000]
        if not source_text:
            raise HTTPException(status_code=422, detail="Input text or account activity data is required")
        folded = source_text.casefold()
        counts = {keyword: folded.count(keyword.casefold()) for keyword in keywords}
        snapshot = {
            "keywords": keywords,
            "keyword_counts": counts,
            "source_characters": len(source_text),
            "activity_sample_size": len(activities),
            "lookback_days": body.lookback_days,
            "account_id": account.id if account else None,
            "data_quality": {
                "sample_sufficient": len(source_text) >= 500,
                "limitations": [
                    *(["输入文本少于500个字符，不能据此判断长期趋势。"] if len(source_text) < 500 else []),
                    *(["账号活动数据不包含X帖子正文，分析仅覆盖已有自动化活动。"] if account else []),
                ],
            },
        }
        title = body.title.strip() or f"关键词分析：{'、'.join(keywords[:3])}"
        item = AIAnalysis(
            workspace_id=workspace_id,
            user_id=user.id,
            provider_id=provider.id,
            account_id=account.id if account else None,
            analysis_type="KEYWORD",
            title=title[:160],
            model=model,
            status="PENDING",
            keywords=keywords,
            input_text=sanitize_analysis_text(body.input_text),
            source_snapshot=snapshot,
            result={},
            summary="",
            created_at=now(),
        )
        db.add(item)
        db.commit()
        return execute(item, provider, keyword_messages(snapshot, source_text), request, user, db)

    @app.delete("/api/ai/analysis/{analysis_id}")
    def delete_analysis(
        analysis_id: str,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        item = visible_analysis(analysis_id, user, db)
        workspace_id = item.workspace_id
        db.delete(item)
        db.commit()
        audit(db, request, action="AI_ANALYSIS_DELETED", result="SUCCESS", user_id=user.id, workspace_id=workspace_id, resource_type="ai_analysis", resource_id=analysis_id)
        return {"deleted": True, "analysis_id": analysis_id}
