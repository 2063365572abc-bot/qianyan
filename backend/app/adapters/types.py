"""Bounded, credential-safe interfaces for external services.

No adapter owns product state or decides whether a task is complete.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

NOT_MODIFIED = object()


class AdapterError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class Observation:
    source: str
    source_id: str
    version: str
    facts: dict[str, Any]
    source_updated_at: str | None = None
    url: str | None = None
    partial: bool = False


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)Bearer\s+[\w./+=-]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)([\w-]*(?:secret|token|password|api_key|access_key)[\w-]*\s*[=:]\s*)[^\s,;]+",
        r"\1[redacted]", text,
    )
    return text


class HTTPAdapter:
    def __init__(
        self, base_url: str, *, client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0,
    ):
        url = httpx.URL(base_url)
        if url.scheme != "https" or url.userinfo or url.query or url.fragment:
            raise AdapterError("configuration", "Adapter requires a credential-free HTTPS base URL.")
        self.base_url = base_url.rstrip("/")
        self.total_timeout = max(1.0, min(timeout, 90.0))
        self.timeout = httpx.Timeout(self.total_timeout, connect=min(10.0, self.total_timeout))
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(transport=transport, follow_redirects=False)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def request_json(
        self, method: str, path: str, *, headers: dict | None = None,
        params: dict | None = None, payload: dict | None = None,
        max_bytes: int = 1_048_576, allow_not_modified: bool = False,
    ) -> tuple[Any, httpx.Headers]:
        # Never put provider response bodies, request URLs or exception messages
        # in product errors: those can contain tokens and private prompts.
        try:
            async with asyncio.timeout(self.total_timeout):
                return await self._read_json(method, path, headers, params, payload, max_bytes, allow_not_modified)
        except (httpx.TimeoutException, TimeoutError):
            raise AdapterError("timeout", "External provider timed out.", True) from None
        except httpx.HTTPError:
            raise AdapterError("network", "External provider is unreachable.", True) from None

    async def _read_json(self, method, path, headers, params, payload, max_bytes, allow_not_modified):
        async with self.client.stream(
            method, self.base_url + path, headers=headers, params=params,
            json=payload, timeout=self.timeout, follow_redirects=False,
        ) as response:
            status = response.status_code
            if status == 304 and allow_not_modified:
                return NOT_MODIFIED, response.headers
            if status >= 400:
                code = {
                    401: "authentication", 403: "permission", 404: "not_found",
                    409: "conflict", 429: "rate_limited",
                }.get(status, "provider_failure")
                retryable = status == 429 or status >= 500
                # GitHub rate limits can use 403.
                if status == 403 and (
                    response.headers.get("x-ratelimit-remaining") == "0"
                    or response.headers.get("retry-after")
                ):
                    code, retryable = "rate_limited", True
                raise AdapterError(code, f"External provider returned HTTP {status}.", retryable)
            if status < 200 or status >= 300:
                raise AdapterError("unexpected_status", "External provider returned an unexpected status.")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise AdapterError("response_too_large", "External response exceeds the read limit.")
            try:
                return json.loads(data), response.headers
            except (ValueError, UnicodeError):
                raise AdapterError("invalid_response", "External provider returned invalid JSON.") from None
