"""Replacement cloud helpers for x_automation_engine.py (lease protocol v2).

Keep one HTTP client per worker process (async: also per event loop).
Do not close a borrowed client until all of that worker's requests have finished.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

MAX_RESPONSE_BYTES = 65_536


def create_sync_dedup_session() -> httpx.Client:
    return httpx.Client(
        timeout=0.8,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        follow_redirects=False,
        trust_env=False,
    )


def create_async_dedup_session() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=0.8,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        follow_redirects=False,
        trust_env=False,
    )


def _prepare(
    server_url: str,
    studio_token: str,
    handle: str,
    device_name: str,
    account_tag: str,
    lease_id: UUID | str,
    timeout: float,
    operation: str,
    agent_token: str,
) -> tuple[str, dict[str, str], dict[str, Any]] | None:
    token = (studio_token or "").strip()
    target = (handle or "").strip().lstrip("@").lower()
    if not target or not server_url or not (token or agent_token):
        return None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    parsed = urlsplit(server_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment
    ):
        raise ValueError("invalid server URL")
    # Validate before normalization to avoid accepting multiple @ prefixes.
    original = handle.strip()
    if original.startswith("@"):
        original = original[1:]
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", original, flags=re.ASCII):
        raise ValueError("invalid target handle")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Studio-Dedup/2.0",
    }
    if token:
        headers["X-Studio-Token"] = token
    if agent_token:
        headers["Authorization"] = f"Bearer {agent_token}"
    payload = {
        "handle": target,
        "device_name": device_name,
        "account_tag": account_tag,
        "action": "follow",
        "lease_id": str(UUID(str(lease_id))),
    }
    return f"{server_url.rstrip('/')}/api/dedup/{operation}", headers, payload


def _interpret(status: int, data: bytes, operation: str):
    if status != 200:
        if operation == "confirm":
            return False
        reason = (
            "AUTH_FAILED_FAIL_OPEN" if status == 401
            else f"HTTP_{status}_FAIL_OPEN"
        )
        return True, reason
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError):
        body = None
    if not isinstance(body, dict):
        return (True, "INVALID_RESPONSE_FAIL_OPEN") if operation == "claim" else False
    if operation == "confirm":
        return body.get("ok") is True
    # Never interpret "false", 0, [] or a missing key as a valid lease response.
    if type(body.get("allowed")) is not bool:
        return True, "INVALID_RESPONSE_FAIL_OPEN"
    reason = str(
        body.get("claimed_by_device") or body.get("reason")
        or body.get("status") or ""
    )
    return body["allowed"], reason


def _sync_request(
    operation: str,
    server_url: str,
    studio_token: str,
    handle: str,
    device_name: str,
    account_tag: str,
    timeout: float,
    *,
    lease_id: UUID | str,
    session: httpx.Client,
    agent_token: str = "",
    control_exception_types: tuple[type[BaseException], ...] = (),
):
    try:
        prepared = _prepare(
            server_url, studio_token, handle, device_name, account_tag,
            lease_id, timeout, operation, agent_token,
        )
        if prepared is None:
            return (True, "SKIPPED_EMPTY") if operation == "claim" else False
        endpoint, headers, payload = prepared
        with session.stream(
            "POST", endpoint, headers=headers, json=payload,
            timeout=timeout, follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                return _interpret(response.status_code, b"", operation)
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise ValueError("dedup response too large")
            return _interpret(response.status_code, bytes(chunks), operation)
    except control_exception_types:
        raise
    except Exception as exc:
        if operation == "confirm":
            return False
        return True, f"ERR_{type(exc).__name__}_FAIL_OPEN"


def _sync_cloud_claim_target(
    server_url: str,
    studio_token: str,
    handle: str,
    device_name: str = "",
    account_tag: str = "",
    timeout: float = 0.8,
    *,
    lease_id: UUID | str | None = None,
    session: httpx.Client | None = None,
    agent_token: str = "",
    control_exception_types: tuple[type[BaseException], ...] = (),
) -> tuple[bool, str]:
    from uuid import uuid4
    use_lease = lease_id if lease_id is not None else uuid4()
    if session is None:
        with create_sync_dedup_session() as temp_session:
            return _sync_request(
                "claim", server_url, studio_token, handle, device_name, account_tag,
                timeout, lease_id=use_lease, session=temp_session, agent_token=agent_token,
                control_exception_types=control_exception_types,
            )
    return _sync_request(
        "claim", server_url, studio_token, handle, device_name, account_tag,
        timeout, lease_id=use_lease, session=session, agent_token=agent_token,
        control_exception_types=control_exception_types,
    )


def _sync_cloud_confirm_target(
    server_url: str,
    studio_token: str,
    handle: str,
    device_name: str = "",
    account_tag: str = "",
    timeout: float = 0.8,
    *,
    lease_id: UUID | str | None = None,
    session: httpx.Client | None = None,
    agent_token: str = "",
    control_exception_types: tuple[type[BaseException], ...] = (),
) -> bool:
    if lease_id is None:
        return False
    if session is None:
        with create_sync_dedup_session() as temp_session:
            return _sync_request(
                "confirm", server_url, studio_token, handle, device_name, account_tag,
                timeout, lease_id=lease_id, session=temp_session, agent_token=agent_token,
                control_exception_types=control_exception_types,
            )
    return _sync_request(
        "confirm", server_url, studio_token, handle, device_name, account_tag,
        timeout, lease_id=lease_id, session=session, agent_token=agent_token,
        control_exception_types=control_exception_types,
    )


class AsyncDedupClient:
    """Borrow a long-lived session; cancellation propagates to the worker.

    The caller owns session.aclose(). No background threads, retry tasks, or
    in-memory lease maps are created. `lease_id` belongs to the logical job.
    """

    def __init__(
        self,
        session: httpx.AsyncClient,
        server_url: str,
        studio_token: str = "",
        *,
        agent_token: str = "",
        timeout: float = 0.8,
        control_exception_types: tuple[type[BaseException], ...] = (),
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.session = session
        self.server_url = server_url
        self.studio_token = studio_token
        self.agent_token = agent_token
        self.timeout = timeout
        self.control_exception_types = control_exception_types

    async def _request(
        self, operation: str, handle: str, lease_id: UUID | str,
        device_name: str, account_tag: str,
    ):
        try:
            # Covers pool acquisition, connect, response headers and body reads.
            async with asyncio.timeout(self.timeout):
                prepared = _prepare(
                    self.server_url, self.studio_token, handle, device_name,
                    account_tag, lease_id, self.timeout, operation, self.agent_token,
                )
                if prepared is None:
                    return (True, "SKIPPED_EMPTY") if operation == "claim" else False
                endpoint, headers, payload = prepared
                async with self.session.stream(
                    "POST", endpoint, headers=headers, json=payload,
                    timeout=self.timeout, follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        return _interpret(response.status_code, b"", operation)
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise ValueError("dedup response too large")
                    return _interpret(response.status_code, bytes(chunks), operation)
        except self.control_exception_types:
            raise
        except Exception as exc:
            return (
                (True, f"ERR_{type(exc).__name__}_FAIL_OPEN")
                if operation == "claim" else False
            )

    async def claim(
        self, handle: str, lease_id: UUID | str,
        device_name: str = "", account_tag: str = "",
    ) -> tuple[bool, str]:
        return await self._request(
            "claim", handle, lease_id, device_name, account_tag,
        )

    async def confirm(
        self, handle: str, lease_id: UUID | str,
        device_name: str = "", account_tag: str = "",
    ) -> bool:
        return await self._request(
            "confirm", handle, lease_id, device_name, account_tag,
        )
