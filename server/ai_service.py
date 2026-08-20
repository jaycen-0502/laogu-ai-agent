from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Callable, Iterator

import httpx

from .ai_provider import ProviderConnectionError, validate_provider_destination


class AIRequestError(RuntimeError):
    pass


class AIRequestTimeout(AIRequestError):
    pass


class AIRequestCancelled(AIRequestError):
    pass


class AIEndpointUnsupported(AIRequestError):
    pass


@dataclass(frozen=True)
class AIUsageResult:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatRunHandle:
    def __init__(self):
        self.cancel_event = threading.Event()
        self._closer: Callable[[], None] | None = None
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def set_closer(self, closer: Callable[[], None] | None) -> None:
        with self._lock:
            self._closer = closer
            cancelled = self.cancelled
        if cancelled and closer:
            try:
                closer()
            except Exception:
                pass

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            closer = self._closer
        if closer:
            try:
                closer()
            except Exception:
                pass


class ChatRunRegistry:
    def __init__(self):
        self._runs: dict[str, ChatRunHandle] = {}
        self._lock = threading.Lock()

    def begin(self, session_id: str) -> ChatRunHandle | None:
        with self._lock:
            if session_id in self._runs:
                return None
            handle = ChatRunHandle()
            self._runs[session_id] = handle
            return handle

    def stop(self, session_id: str) -> bool:
        with self._lock:
            handle = self._runs.get(session_id)
        if not handle:
            return False
        handle.cancel()
        return True

    def finish(self, session_id: str, handle: ChatRunHandle) -> None:
        with self._lock:
            if self._runs.get(session_id) is handle:
                self._runs.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._runs


def _usage(payload: dict) -> AIUsageResult:
    value = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        value = {}
    prompt = int(value.get("input_tokens") or value.get("prompt_tokens") or 0)
    completion = int(value.get("output_tokens") or value.get("completion_tokens") or 0)
    total = int(value.get("total_tokens") or prompt + completion)
    return AIUsageResult(prompt, completion, total)


def _output_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


class AIService:
    def __init__(self, timeout_seconds: int, max_output_tokens: int, *, production: bool):
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.production = production

    def stream(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        handle: ChatRunHandle,
    ) -> Iterator[dict]:
        try:
            validate_provider_destination(base_url, production=self.production)
        except ProviderConnectionError as exc:
            raise AIRequestError(str(exc)) from exc
        try:
            yield from self._stream_responses(
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                handle=handle,
            )
        except AIEndpointUnsupported:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled")
            yield from self._stream_chat_completions(
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                handle=handle,
            )

    def _stream_responses(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        handle: ChatRunHandle,
    ) -> Iterator[dict]:
        payload = {
            "model": model,
            "input": messages,
            "stream": True,
            "max_output_tokens": self.max_output_tokens,
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds))
        client = httpx.Client(timeout=timeout, follow_redirects=False)
        handle.set_closer(client.close)
        try:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled")
            with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "text/event-stream, application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                handle.set_closer(response.close)
                if response.status_code == 401:
                    raise AIRequestError("AI provider authentication failed")
                if response.status_code in {404, 405}:
                    raise AIEndpointUnsupported("Responses API is not supported")
                if response.status_code >= 400:
                    raise AIRequestError(f"AI provider returned HTTP {response.status_code}")
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    data = response.json()
                    text = _output_text(data)
                    if text:
                        yield {"type": "delta", "delta": text}
                    yield {"type": "completed", "usage": _usage(data)}
                    return
                event_name = ""
                completed = False
                for line in response.iter_lines():
                    if handle.cancelled:
                        raise AIRequestCancelled("AI request cancelled")
                    if not line:
                        event_name = ""
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    kind = str(data.get("type") or event_name)
                    if kind == "response.output_text.delta":
                        delta = data.get("delta")
                        if isinstance(delta, str) and delta:
                            yield {"type": "delta", "delta": delta}
                    elif kind == "response.completed":
                        completed = True
                        response_data = data.get("response") if isinstance(data.get("response"), dict) else data
                        yield {"type": "completed", "usage": _usage(response_data)}
                    elif kind in {"error", "response.failed", "response.incomplete"}:
                        raise AIRequestError("AI provider failed to complete the response")
                if handle.cancelled:
                    raise AIRequestCancelled("AI request cancelled")
                if not completed:
                    raise AIRequestError("AI provider stream ended unexpectedly")
        except AIRequestError:
            raise
        except httpx.TimeoutException as exc:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled") from exc
            raise AIRequestTimeout("AI request timed out") from exc
        except httpx.HTTPError as exc:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled") from exc
            raise AIRequestError("AI provider connection failed") from exc
        except (ValueError, TypeError) as exc:
            raise AIRequestError("AI provider returned an invalid response") from exc
        finally:
            handle.set_closer(None)
            client.close()

    def _stream_chat_completions(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        handle: ChatRunHandle,
    ) -> Iterator[dict]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self.max_output_tokens,
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds))
        client = httpx.Client(timeout=timeout, follow_redirects=False)
        handle.set_closer(client.close)
        try:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled")
            with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "text/event-stream, application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                handle.set_closer(response.close)
                if response.status_code == 401:
                    raise AIRequestError("AI provider authentication failed")
                if response.status_code >= 400:
                    raise AIRequestError(f"AI provider returned HTTP {response.status_code}")
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    data = response.json()
                    choices = data.get("choices") if isinstance(data, dict) else None
                    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
                    text = message.get("content") if isinstance(message, dict) else ""
                    if isinstance(text, str) and text:
                        yield {"type": "delta", "delta": text}
                    yield {"type": "completed", "usage": _usage(data)}
                    return
                usage = AIUsageResult()
                completed = False
                for line in response.iter_lines():
                    if handle.cancelled:
                        raise AIRequestCancelled("AI request cancelled")
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        completed = True
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data.get("usage"), dict):
                        usage = _usage(data)
                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield {"type": "delta", "delta": content}
                    if choice.get("finish_reason") is not None:
                        completed = True
                if handle.cancelled:
                    raise AIRequestCancelled("AI request cancelled")
                if not completed:
                    raise AIRequestError("AI provider stream ended unexpectedly")
                yield {"type": "completed", "usage": usage}
        except AIRequestError:
            raise
        except httpx.TimeoutException as exc:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled") from exc
            raise AIRequestTimeout("AI request timed out") from exc
        except httpx.HTTPError as exc:
            if handle.cancelled:
                raise AIRequestCancelled("AI request cancelled") from exc
            raise AIRequestError("AI provider connection failed") from exc
        except (ValueError, TypeError) as exc:
            raise AIRequestError("AI provider returned an invalid response") from exc
        finally:
            handle.set_closer(None)
            client.close()
