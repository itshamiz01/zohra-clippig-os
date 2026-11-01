from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    operation: str
    system_prompt: str
    user_content: str
    max_output_tokens: int = 1024
    temperature: float = 0.2
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class RawGeneration:
    text: str
    provider: str
    model: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationMetadata:
    provider: str
    model: str
    operation: str
    latency_ms: int
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: Decimal
    cost_is_estimate: bool
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class TextGenerationResult:
    text: str
    metadata: GenerationMetadata


class ModelAdapter(Protocol):
    provider_name: str
    model_name: str

    async def generate(self, request: GenerationRequest) -> RawGeneration: ...

    async def close(self) -> None: ...
