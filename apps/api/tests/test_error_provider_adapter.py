import httpx
import pytest

from clipos.gateways.models.adapters import OpenAICompatibleAdapter
from clipos.gateways.models.contracts import GenerationRequest
from clipos.gateways.models.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
)

pytestmark = pytest.mark.unit


async def test_mocked_openai_adapter_normalizes_and_never_echoes_provider_body() -> None:
    secret_body = "provider leaked secret marker"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(401, text=secret_body)

    adapter = OpenAICompatibleAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderAuthenticationError) as caught:
        await adapter.generate(
            GenerationRequest(
                operation="fixture",
                system_prompt="system",
                user_content="untrusted",
            )
        )
    assert secret_body not in str(caught.value)
    await adapter.close()


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(429, ProviderRateLimitError), (400, ProviderResponseError)],
)
async def test_mocked_provider_status_is_normalized(status, error_type) -> None:
    adapter = OpenAICompatibleAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="unsafe body")),
    )
    with pytest.raises(error_type):
        await adapter.generate(
            GenerationRequest(
                operation="fixture",
                system_prompt="system",
                user_content="data",
            )
        )
    await adapter.close()


async def test_mocked_openai_adapter_bounds_stream_and_records_request_id() -> None:
    valid_body = (
        '{"choices":[{"message":{"content":"ok"}}],'
        '"usage":{"prompt_tokens":4,"completion_tokens":2}}'
    )

    async def valid_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=valid_body,
            headers={"x-request-id": "provider-request-7"},
        )

    adapter = OpenAICompatibleAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=1,
        response_max_bytes=1024,
        transport=httpx.MockTransport(valid_handler),
    )
    result = await adapter.generate(
        GenerationRequest(
            operation="fixture.success",
            system_prompt="system",
            user_content="untrusted data",
        )
    )
    assert result.text == "ok"
    assert result.provider_request_id == "provider-request-7"
    await adapter.close()

    oversized = OpenAICompatibleAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=1,
        response_max_bytes=32,
        transport=httpx.MockTransport(valid_handler),
    )
    with pytest.raises(ProviderResponseError, match="byte limit"):
        await oversized.generate(
            GenerationRequest(
                operation="fixture.oversized",
                system_prompt="system",
                user_content="untrusted data",
            )
        )
    await oversized.close()
