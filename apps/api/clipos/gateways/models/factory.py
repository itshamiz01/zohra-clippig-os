from clipos.config import Settings
from clipos.gateways.models.adapters import (
    AnthropicAdapter,
    DisabledModelAdapter,
    OpenAICompatibleAdapter,
)
from clipos.gateways.models.contracts import ModelAdapter
from clipos.gateways.models.gateway import ModelGateway


def build_model_gateway(settings: Settings) -> ModelGateway:
    adapter: ModelAdapter
    if settings.model_provider == "disabled":
        adapter = DisabledModelAdapter()
    elif settings.model_provider in {"openai", "openai_compatible"}:
        assert settings.openai_api_key is not None
        assert settings.openai_model is not None
        adapter = OpenAICompatibleAdapter(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.model_timeout_seconds,
            provider_name=settings.model_provider,
            response_max_bytes=settings.model_response_max_bytes,
        )
    elif settings.model_provider == "anthropic":
        assert settings.anthropic_api_key is not None
        assert settings.anthropic_model is not None
        adapter = AnthropicAdapter(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.anthropic_model,
            timeout_seconds=settings.model_timeout_seconds,
            response_max_bytes=settings.model_response_max_bytes,
        )
    else:  # Configuration validation makes this unreachable.
        raise ValueError("unsupported model provider")
    return ModelGateway(
        adapter,
        max_retries=settings.model_max_retries,
        input_cost_per_million=settings.model_input_cost_per_million,
        output_cost_per_million=settings.model_output_cost_per_million,
        max_input_chars=settings.model_request_max_input_chars,
        max_output_tokens=settings.model_request_max_output_tokens,
        max_response_chars=settings.model_response_max_chars,
    )
