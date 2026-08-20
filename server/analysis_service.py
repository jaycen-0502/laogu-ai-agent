from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from .ai_service import AIRequestError, AIService, AIUsageResult, ChatRunHandle


_INLINE_SECRET = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+"
    r"|(sk-[A-Za-z0-9_-]{8,})"
    r"|(lag_[A-Za-z0-9_-]{12,})"
    r"|((?:api[_ -]?key|authorization|jwt|agent[_ -]?token|x[_ -]?token|cookie|password)"
    r"\s*(?:is|[:=])\s*)\S+"
)


def sanitize_analysis_text(value: str) -> str:
    text = str(value or "").strip()

    def replace(match: re.Match) -> str:
        prefix = match.group(1) or match.group(4) or ""
        return f"{prefix}[REDACTED]"

    return _INLINE_SECRET.sub(replace, text)


def parse_analysis_json(value: str) -> dict:
    text = sanitize_analysis_text(value)
    candidates = [text]
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            candidates.append("\n".join(lines[1:-1]))
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return {
        "overview": text[:10000],
        "data_quality": {
            "level": "UNKNOWN",
            "warnings": ["AI未返回标准JSON，已保留原始分析文本。"],
        },
    }


def account_messages(snapshot: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是账号运行健康分析助手。只能根据给定快照判断，不得虚构粉丝、帖子、互动或收益数据。"
                "样本少于10条时必须明确写入data_quality.warnings。只返回一个JSON对象，字段为："
                "overview、health_score(0-100)、data_quality{level,warnings[]}、strengths[]、risks[]、recommendations[]。"
            ),
        },
        {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
    ]


def keyword_messages(snapshot: dict, source_text: str) -> list[dict[str, str]]:
    payload = {
        "instruction": "解释确定性统计结果，识别主题、语境、风险并给出行动建议。不得编造未出现的内容或趋势。",
        "snapshot": snapshot,
        "source_text": source_text[:16000],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是关键词分析助手。keyword_counts是服务端确定性计数，不得修改。"
                "只返回一个JSON对象，字段为：overview、data_quality{level,warnings[]}、"
                "keyword_findings[{keyword,context,sentiment,notes[]}],themes[]、risks[]、recommendations[]。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


@dataclass
class AIAnalysisRunResult:
    result: dict = field(default_factory=dict)
    summary: str = ""
    usage: AIUsageResult = field(default_factory=AIUsageResult)


class AIAnalysisService:
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service

    def run(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
    ) -> AIAnalysisRunResult:
        parts: list[str] = []
        usage = AIUsageResult()
        for event in self.ai_service.stream(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            handle=ChatRunHandle(),
        ):
            if event.get("type") == "delta":
                parts.append(str(event.get("delta") or ""))
            elif event.get("type") == "completed" and isinstance(event.get("usage"), AIUsageResult):
                usage = event["usage"]
        output = sanitize_analysis_text("".join(parts))
        if not output:
            raise AIRequestError("AI provider returned an empty analysis")
        result = parse_analysis_json(output)
        summary = str(result.get("overview") or result.get("summary") or output)[:2000]
        return AIAnalysisRunResult(result=result, summary=summary, usage=usage)
