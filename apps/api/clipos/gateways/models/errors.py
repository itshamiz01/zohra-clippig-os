class ModelGatewayError(RuntimeError):
    category = "model_gateway_error"


class ProviderDisabledError(ModelGatewayError):
    category = "provider_disabled"


class ProviderAuthenticationError(ModelGatewayError):
    category = "provider_authentication"


class ProviderRateLimitError(ModelGatewayError):
    category = "provider_rate_limit"


class ProviderTimeoutError(ModelGatewayError):
    category = "provider_timeout"


class ProviderTransientError(ModelGatewayError):
    category = "provider_transient"


class ProviderResponseError(ModelGatewayError):
    category = "provider_response_invalid"


class StructuredOutputError(ModelGatewayError):
    category = "structured_output_invalid"


class ModelInputLimitError(ModelGatewayError):
    category = "model_input_limit"
