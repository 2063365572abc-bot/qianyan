"""OpenAI-compatible inference for the explicitly configured provider."""

from __future__ import annotations

from typing import Any

import httpx

from .types import AdapterError, HTTPAdapter


class TokenFactoryAdapter(HTTPAdapter):
    def __init__(
        self, api_key: str, model_id: str,
        base_url: str = "https://api.tokenfactory.nebius.com/v1", *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None, timeout: float = 60.0,
    ):
        if not api_key or not model_id:
            raise AdapterError("configuration", "Model API key and model ID are required.")
        super().__init__(base_url, client=client, transport=transport, timeout=timeout)
        self._api_key = api_key
        self.model_id = model_id

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}

    async def list_models(self) -> list[str]:
        data, _ = await self.request_json("GET", "/models", headers=self._headers)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise AdapterError("invalid_response", "Provider returned an invalid model list.")
        return [row["id"] for row in data["data"] if isinstance(row, dict) and isinstance(row.get("id"), str)]

    async def verify_model(self, *, require_nvidia: bool = True) -> str:
        if require_nvidia and not self.model_id.lower().startswith("nvidia/"):
            raise AdapterError("model_configuration", "This project requires an NVIDIA model ID.")
        if self.model_id not in await self.list_models():
            raise AdapterError("model_unavailable", "Configured model is not in the account model catalog.")
        return self.model_id

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
        *, max_tokens: int = 2048, tool_choice: str | dict | None = None,
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        if not messages or len(messages) > 100:
            raise AdapterError("input", "Inference requires 1–100 messages.")
        payload: dict[str, Any] = {
            "model": self.model_id, "messages": messages,
            "max_tokens": max(1, min(max_tokens, 4096)), "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        data, _ = await self.request_json("POST", "/chat/completions", headers=self._headers, payload=payload)
        try:
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError
            content, calls = message.get("content"), message.get("tool_calls", [])
            if content is not None and not isinstance(content, str):
                raise TypeError
            if not isinstance(calls, list) or (content is None and not calls):
                raise TypeError
            return {
                "content": content, "tool_calls": calls,
                "usage": data.get("usage") or {}, "model": data.get("model") or self.model_id,
            }
        except (KeyError, IndexError, TypeError):
            raise AdapterError("invalid_response", "Provider returned no valid completion.") from None
