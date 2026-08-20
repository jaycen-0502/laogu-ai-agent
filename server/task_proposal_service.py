from __future__ import annotations

import json

from .analysis_service import AIAnalysisRunResult, AIAnalysisService, parse_analysis_json, sanitize_analysis_text


def task_proposal_messages(request_text: str, scripts: list[dict], profiles: list[dict]) -> list[dict[str, str]]:
    payload = {
        "request": request_text,
        "enabled_scripts": scripts,
        "available_profiles": profiles,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是Laogu任务规划助手。只能从enabled_scripts和available_profiles中选择，不能编造ID，"
                "不能生成脚本源代码，不能执行任务。返回一个JSON对象，字段为：summary、script_id、"
                "script_version_id、profile_ids[]、params{}、timeout、reason、risk_notes[]、needs_confirmation。"
                "如果没有合适脚本，script_id和script_version_id必须为null，needs_confirmation为false。"
                "必须将needs_confirmation设为true才能提出可执行计划。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class AITaskProposalService:
    def __init__(self, analysis_service: AIAnalysisService):
        self.analysis_service = analysis_service

    def run(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        request_text: str,
        scripts: list[dict],
        profiles: list[dict],
    ) -> AIAnalysisRunResult:
        output = self.analysis_service.run(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=task_proposal_messages(request_text, scripts, profiles),
        )
        result = parse_analysis_json(json.dumps(output.result, ensure_ascii=False)) if not isinstance(output.result, dict) else output.result
        normalized = {
            "summary": sanitize_analysis_text(str(result.get("summary") or result.get("overview") or ""))[:2000],
            "script_id": str(result.get("script_id") or "").strip() or None,
            "script_version_id": str(result.get("script_version_id") or "").strip() or None,
            "profile_ids": [str(item).strip() for item in result.get("profile_ids", []) if str(item).strip()][:100] if isinstance(result.get("profile_ids"), list) else [],
            "params": result.get("params") if isinstance(result.get("params"), dict) else {},
            "timeout": int(result.get("timeout") or 60),
            "reason": sanitize_analysis_text(str(result.get("reason") or ""))[:2000],
            "risk_notes": [sanitize_analysis_text(str(item))[:500] for item in result.get("risk_notes", [])[:20]] if isinstance(result.get("risk_notes"), list) else [],
            "needs_confirmation": bool(result.get("needs_confirmation")),
        }
        output.result = normalized
        output.summary = normalized["summary"] or normalized["reason"] or "已生成任务计划"
        return output
