from __future__ import annotations

import json

from .ai_service import AIRequestError
from .analysis_service import AIAnalysisRunResult, AIAnalysisService, sanitize_analysis_text


TONE_NAMES = {
    "PROFESSIONAL": "专业、克制、准确",
    "FRIENDLY": "友好、自然、有礼貌",
    "CONCISE": "简洁、直接、不啰嗦",
    "PERSUASIVE": "有说服力，但不夸大、不施压",
}

LANGUAGE_NAMES = {
    "AUTO": "跟随原文主要语言",
    "ZH": "简体中文",
    "EN": "English",
}


def writing_analysis_messages(source_text: str, context_text: str) -> list[dict[str, str]]:
    payload = {
        "source_text": source_text,
        "context": context_text,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是社交媒体话术分析助手。只分析给定文本，不得虚构作者身份、历史、粉丝或平台数据。"
                "只返回一个JSON对象，字段为：overview、intent、sentiment、tone、key_points[]、"
                "risks[]、reply_strategy[]、data_quality{level,warnings[]}。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def reply_generation_messages(
    source_text: str,
    context_text: str,
    parameters: dict,
) -> list[dict[str, str]]:
    payload = {
        "source_text": source_text,
        "context": context_text,
        "objective": parameters.get("objective", ""),
        "brand_voice": parameters.get("brand_voice", ""),
        "tone": TONE_NAMES.get(str(parameters.get("tone")), str(parameters.get("tone") or "")),
        "language": LANGUAGE_NAMES.get(str(parameters.get("language")), str(parameters.get("language") or "")),
        "variant_count": parameters.get("variant_count"),
        "max_characters_per_reply": parameters.get("max_characters"),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是社交媒体回复草稿助手。生成的是待人工审核的候选草稿，绝不能声称已经发布或执行操作。"
                "不得编造事实、价格、承诺、身份或私密信息；遇到信息不足应使用谨慎表达。"
                "严格遵守候选数量和每条最大字符数。只返回一个JSON对象，字段为："
                "overview、strategy、replies[{text,tone,reason}]、safety_notes[]。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class AIWritingService:
    def __init__(self, analysis_service: AIAnalysisService):
        self.analysis_service = analysis_service

    def analyze(self, *, base_url: str, api_key: str, model: str, source_text: str, context_text: str) -> AIAnalysisRunResult:
        return self.analysis_service.run(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=writing_analysis_messages(source_text, context_text),
        )

    def generate(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        source_text: str,
        context_text: str,
        parameters: dict,
    ) -> AIAnalysisRunResult:
        output = self.analysis_service.run(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=reply_generation_messages(source_text, context_text, parameters),
        )
        raw_replies = output.result.get("replies")
        if not isinstance(raw_replies, list):
            raise AIRequestError("AI provider did not return reply drafts")
        limit = int(parameters.get("variant_count") or 1)
        max_characters = int(parameters.get("max_characters") or 280)
        replies: list[dict] = []
        seen: set[str] = set()
        for value in raw_replies:
            if isinstance(value, dict):
                text = sanitize_analysis_text(str(value.get("text") or ""))
                tone = sanitize_analysis_text(str(value.get("tone") or parameters.get("tone") or ""))[:80]
                reason = sanitize_analysis_text(str(value.get("reason") or ""))[:500]
            else:
                text = sanitize_analysis_text(str(value or ""))
                tone = str(parameters.get("tone") or "")
                reason = ""
            text = text[:max_characters].strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            replies.append({
                "text": text,
                "tone": tone,
                "reason": reason,
                "character_count": len(text),
            })
            if len(replies) >= limit:
                break
        if not replies:
            raise AIRequestError("AI provider returned no usable reply drafts")
        safety_notes = output.result.get("safety_notes")
        normalized_notes = [sanitize_analysis_text(str(item))[:500] for item in safety_notes[:20]] if isinstance(safety_notes, list) else []
        output.result = {
            "overview": sanitize_analysis_text(str(output.result.get("overview") or ""))[:2000],
            "strategy": sanitize_analysis_text(str(output.result.get("strategy") or ""))[:2000],
            "replies": replies,
            "safety_notes": normalized_notes,
        }
        output.summary = output.result["overview"] or output.result["strategy"] or f"已生成{len(replies)}条回复草稿"
        return output
