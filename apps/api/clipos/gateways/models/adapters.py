from __future__ import annotations

import json
from typing import Any

import httpx

from clipos.gateways.models.contracts import (
    GenerationRequest,
    ProviderUsage,
    RawGeneration,
)
from clipos.gateways.models.errors import (
    ProviderAuthenticationError,
    ProviderDisabledError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientError,
)


class DisabledModelAdapter:
    provider_name = "disabled"
    model_name = "none"

    async def generate(self, request: GenerationRequest) -> RawGeneration:
        del request
        raise ProviderDisabledError("model provider is disabled")

    async def close(self) -> None:
        return None


class OpenAICompatibleAdapter:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        provider_name: str = "openai",
        response_max_bytes: int = 4 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model
        self.response_max_bytes = response_max_bytes
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def generate(self, request: GenerationRequest) -> RawGeneration:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_content},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "clipos_structured_response",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        body, headers = await _post_json_bounded(
            self._client,
            "chat/completions",
            payload,
            max_bytes=self.response_max_bytes,
        )
        try:
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            if not isinstance(text, str):
                raise TypeError("content must be text")
            return RawGeneration(
                text=text,
                provider=self.provider_name,
                model=self.model_name,
                usage=ProviderUsage(
                    input_tokens=_optional_int(usage.get("prompt_tokens")),
                    output_tokens=_optional_int(usage.get("completion_tokens")),
                ),
                provider_request_id=headers.get("x-request-id"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "model provider returned an invalid response shape"
            ) from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("model provider rejected credentials or scope")
        if response.status_code == 429:
            raise ProviderRateLimitError("model provider rate limit reached")
        if response.status_code >= 500:
            raise ProviderTransientError("model provider is temporarily unavailable")
        if response.status_code >= 400:
            raise ProviderResponseError(f"model provider rejected request ({response.status_code})")

    async def close(self) -> None:
        await self._client.aclose()


class AnthropicAdapter:
    provider_name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        response_max_bytes: int = 4 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model_name = model
        self.response_max_bytes = response_max_bytes
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def generate(self, request: GenerationRequest) -> RawGeneration:
        system_prompt = request.system_prompt
        if request.json_schema:
            system_prompt += (
                "\nReturn only one JSON value matching this schema; do not use markdown fences:\n"
                + json.dumps(request.json_schema, separators=(",", ":"))
            )
        payload = {
            "model": self.model_name,
            "system": system_prompt,
            "messages": [{"role": "user", "content": request.user_content}],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        body, headers = await _post_json_bounded(
            self._client,
            "/v1/messages",
            payload,
            max_bytes=self.response_max_bytes,
        )
        try:
            text_blocks = [
                block["text"] for block in body["content"] if block.get("type") == "text"
            ]
            text = "".join(text_blocks)
            if not text:
                raise ValueError("empty text")
            usage = body.get("usage", {})
            return RawGeneration(
                text=text,
                provider=self.provider_name,
                model=self.model_name,
                usage=ProviderUsage(
                    input_tokens=_optional_int(usage.get("input_tokens")),
                    output_tokens=_optional_int(usage.get("output_tokens")),
                ),
                provider_request_id=headers.get("request-id"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "model provider returned an invalid response shape"
            ) from exc

    async def close(self) -> None:
        await self._client.aclose()


async def _post_json_bounded(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    *,
    max_bytes: int,
) -> tuple[dict[str, Any], httpx.Headers]:
    try:
        request = client.build_request("POST", path, json=payload)
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("model provider timed out") from exc
    except httpx.TransportError as exc:
        raise ProviderTransientError("model provider transport failed") from exc
    try:
        OpenAICompatibleAdapter._raise_for_status(response)
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ProviderResponseError(
                    "model provider response exceeds the configured byte limit"
                )
        try:
            body = json.loads(bytes(content))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderResponseError("model provider returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderResponseError("model provider returned an invalid response shape")
        return body, response.headers
    finally:
        await response.aclose()


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
