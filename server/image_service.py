from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

import httpx

from .ai_provider import ProviderConnectionError, validate_provider_destination


class AIImageRequestError(RuntimeError):
    pass


class AIImageRequestTimeout(AIImageRequestError):
    pass


class AIImageResponseTooLarge(AIImageRequestError):
    pass


@dataclass(frozen=True)
class AIImageResult:
    content: bytes
    mime_type: str
    prompt_tokens: int = 0
    image_tokens: int = 0
    total_tokens: int = 0


def _usage(payload: dict) -> tuple[int, int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0
    prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    image = int(usage.get("output_tokens") or usage.get("image_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + image)
    return prompt, image, total


def _mime_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise AIImageRequestError("AI provider returned an unsupported image format")


class AIImageService:
    def __init__(self, timeout_seconds: int, max_bytes: int, *, production: bool):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.production = production

    def generate(
        self,
        *,
        base_url: str,
        api_key: str,
        prompt: str,
        size: str,
        quality: str,
        model: str = "gpt-image-2",
    ) -> AIImageResult:
        try:
            validate_provider_destination(base_url, production=self.production)
        except ProviderConnectionError as exc:
            raise AIImageRequestError(str(exc)) from exc
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds))
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                    "quality": quality,
                    "n": 1,
                },
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise AIImageRequestTimeout("AI image request timed out") from exc
        except httpx.HTTPError as exc:
            raise AIImageRequestError("AI image provider connection failed") from exc
        if response.status_code == 401:
            raise AIImageRequestError("AI image provider authentication failed")
        if response.status_code >= 400:
            raise AIImageRequestError(f"AI image provider returned HTTP {response.status_code}")
        try:
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            first = data[0] if isinstance(data, list) and data else None
            encoded = first.get("b64_json") if isinstance(first, dict) else None
            if not isinstance(encoded, str) or not encoded:
                raise ValueError
            if len(encoded) > ((self.max_bytes + 2) // 3) * 4 + 16:
                raise AIImageResponseTooLarge("AI image response is too large")
            content = base64.b64decode(encoded, validate=True)
        except AIImageResponseTooLarge:
            raise
        except (ValueError, TypeError, KeyError, binascii.Error) as exc:
            raise AIImageRequestError("AI image provider returned an invalid response") from exc
        if not content:
            raise AIImageRequestError("AI image provider returned an empty image")
        if len(content) > self.max_bytes:
            raise AIImageResponseTooLarge("AI image response is too large")
        prompt_tokens, image_tokens, total_tokens = _usage(payload)
        return AIImageResult(
            content=content,
            mime_type=_mime_type(content),
            prompt_tokens=prompt_tokens,
            image_tokens=image_tokens,
            total_tokens=total_tokens,
        )
