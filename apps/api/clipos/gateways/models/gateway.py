from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from clipos.gateways.models.contracts import (
    GenerationMetadata,
    GenerationRequest,
    ModelAdapter,
    TextGenerationResult,
)
from clipos.gateways.models.errors import (
    ModelInputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientError,
    StructuredOutputError,
)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredGenerationResult(Generic[T]):
    value: T
    metadata: GenerationMetadata


class ModelGateway:
    """Single model boundary with bounded retries and strict output validation."""

    RETRYABLE = (ProviderRateLimitError, ProviderTimeoutError, ProviderTransientError)

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        max_retries: int = 2,
        input_cost_per_million: float = 0,
        output_cost_per_million: float = 0,
        max_input_chars: int = 80_000,
        max_output_tokens: int = 4_096,
        max_response_chars: int = 1_000_000,
    ) -> None:
        self.adapter = adapter
        self.max_retries = max_retries
        self.input_rate = Decimal(str(input_cost_per_million)) / Decimal(1_000_000)
        self.output_rate = Decimal(str(output_cost_per_million)) / Decimal(1_000_000)
        self.max_input_chars = max_input_chars
        self.max_output_tokens = max_output_tokens
        self.max_response_chars = max_response_chars

    async def generate_text(self, request: GenerationRequest) -> TextGenerationResult:
        schema_chars = (
            len(json.dumps(request.json_schema, separators=(",", ":")))
            if request.json_schema
            else 0
        )
        input_chars = len(request.system_prompt) + len(request.user_content) + schema_chars
        if input_chars > self.max_input_chars:
            raise ModelInputLimitError("model request exceeds the configured input limit")
        if request.max_output_tokens < 1 or request.max_output_tokens > self.max_output_tokens:
            raise ModelInputLimitError("model request exceeds the configured output-token limit")
        started = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                result = await self.adapter.generate(request)
                if len(result.text) > self.max_response_chars:
                    raise ProviderResponseError(
                        "model provider response exceeds the configured text limit"
                    )
                break
            except self.RETRYABLE:
                if attempts > self.max_retries:
                    raise
                jitter = secrets.randbelow(100) / 1000
                delay = min(4.0, (2 ** (attempts - 1)) * 0.25) + jitter
                await asyncio.sleep(delay)
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        estimated_cost = self._cost(result.usage.input_tokens, result.usage.output_tokens)
        metadata = GenerationMetadata(
            provider=result.provider,
            model=result.model,
            operation=request.operation,
            latency_ms=elapsed_ms,
            attempts=attempts,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost=estimated_cost,
            cost_is_estimate=True,
            provider_request_id=result.provider_request_id,
        )
        return TextGenerationResult(text=result.text, metadata=metadata)

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_content: str,
        output_schema: type[T],
        max_output_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> StructuredGenerationResult[T]:
        request = GenerationRequest(
            operation=operation,
            system_prompt=system_prompt,
            user_content=user_content,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            json_schema=output_schema.model_json_schema(),
        )
        generated = await self.generate_text(request)
        try:
            raw = json.loads(generated.text)
            value = output_schema.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise StructuredOutputError("model output failed strict schema validation") from exc
        return StructuredGenerationResult(value=value, metadata=generated.metadata)

    def estimate_cost_upper_bound(self, *, input_chars: int, output_tokens: int) -> Decimal:
        """Conservatively treat each input character as one billable token."""
        return self._cost(max(0, input_chars), max(0, output_tokens))

    def _cost(self, input_tokens: int | None, output_tokens: int | None) -> Decimal:
        input_cost = Decimal(input_tokens or 0) * self.input_rate
        output_cost = Decimal(output_tokens or 0) * self.output_rate
        return (input_cost + output_cost).quantize(Decimal("0.00000001"))

    async def close(self) -> None:
        await self.adapter.close()
