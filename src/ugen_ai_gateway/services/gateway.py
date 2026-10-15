from time import perf_counter
from typing import Any, cast

from openai.types.chat import ChatCompletionMessageParam

from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer
from ugen_ai_gateway.analysis.pii import redact_personal_data
from ugen_ai_gateway.analysis.policy import RoutingPolicy
from ugen_ai_gateway.analysis.schemas import TokenUsage
from ugen_ai_gateway.api.schemas import (
    ChatRequest,
    ChatResponse,
    PerformanceStats,
    RoutingInfo,
    UsageStats,
)
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.providers.cloud import CloudProvider
from ugen_ai_gateway.providers.local import LocalProvider
from ugen_ai_gateway.services.errors import (
    GatewayConfigurationError,
    InvalidChatRequest,
    PrivacyRedactionError,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    RequestTooLarge,
)

MAX_ANALYSIS_INPUT_CHARACTERS = 32_000
SUPPORTED_PERSONAL_DATA_TYPES = {
    "email",
    "taiwan_mobile",
    "taiwan_id",
    "credit_card",
}


class GatewayService:
    """Analyze requests and route them to the appropriate model provider."""

    def __init__(
        self,
        *,
        settings: Settings,
        local_provider: LocalProvider,
        cloud_provider: CloudProvider | None,
        analyzer: RequestAnalyzer,
        policy: RoutingPolicy,
    ) -> None:
        self._settings = settings
        self._local_provider = local_provider
        self._cloud_provider = cloud_provider
        self._analyzer = analyzer
        self._policy = policy

    async def readiness(self) -> bool:
        if not self._settings.local_model:
            return False
        try:
            await self._local_provider.health_check()
            if self._cloud_provider is not None:
                await self._cloud_provider.health_check()
        except Exception:  # noqa: BLE001 - readiness reports any provider failure
            return False
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        total_started_at = perf_counter()
        if not self._settings.local_model:
            raise GatewayConfigurationError("LOCAL_MODEL is not configured")

        user_messages: list[str] = []
        for message in request.messages:
            if message["role"] != "user":
                continue

            content = message.get("content")
            if isinstance(content, str):
                user_messages.append(content)

        user_input = "\n".join(user_messages)

        if not user_input:
            raise InvalidChatRequest("A text user message is required")

        if len(user_input) > MAX_ANALYSIS_INPUT_CHARACTERS:
            raise RequestTooLarge(
                f"User text exceeds the {MAX_ANALYSIS_INPUT_CHARACTERS}-character limit"
            )

        analysis_started_at = perf_counter()
        try:
            analysis = await self._analyzer.analyze(user_input)
        except TimeoutError as exc:
            raise ProviderTimeout("Analysis provider timed out") from exc
        except Exception as exc:
            raise ProviderRequestError(f"Request analysis failed: {exc}") from exc
        analysis_ms = (perf_counter() - analysis_started_at) * 1000

        unredactable_types = {
            data_type.casefold()
            for data_type in analysis.personal_data_types
            if data_type.casefold() not in SUPPORTED_PERSONAL_DATA_TYPES
        }
        if unredactable_types and not analysis.personal_data_values:
            raise PrivacyRedactionError(
                "Detected personal data could not be safely redacted"
            )
        if analysis.contains_personal_data and not (
            analysis.personal_data_types or analysis.personal_data_values
        ):
            raise PrivacyRedactionError(
                "Detected personal data could not be safely redacted"
            )

        decision = self._policy.decide(analysis)

        if decision.provider == "cloud":
            if self._cloud_provider is None:
                raise ProviderUnavailable("Cloud provider is unavailable")

            provider = self._cloud_provider
            model = request.model or self._settings.cloud_model
        else:
            provider = self._local_provider
            model = request.model or self._settings.local_model

        if not model:
            raise GatewayConfigurationError(
                f"No model configured for the {decision.provider} provider"
            )

        messages: list[ChatCompletionMessageParam] = []
        for message in request.messages:
            message_data = cast(dict[str, Any], message)
            redacted_message = message_data.copy()
            content = redacted_message.get("content")
            if isinstance(content, str):
                redacted_message["content"] = redact_personal_data(
                    content,
                    additional_values=analysis.personal_data_values,
                )
            elif content is not None and not isinstance(content, dict):
                redacted_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        redacted_parts.append(part)
                        continue

                    redacted_part = part.copy()
                    if isinstance(redacted_part.get("text"), str):
                        redacted_part["text"] = redact_personal_data(
                            redacted_part["text"],
                            additional_values=analysis.personal_data_values,
                        )
                    redacted_parts.append(redacted_part)

                redacted_message["content"] = redacted_parts

            messages.append(cast(ChatCompletionMessageParam, redacted_message))

        generation_started_at = perf_counter()
        try:
            completion = await provider.complete(
                model=model,
                messages=messages,
                temperature=request.temperature,
            )
        except TimeoutError as exc:
            raise ProviderTimeout("Model provider timed out") from exc
        except Exception as exc:
            raise ProviderRequestError(f"Model provider request failed: {exc}") from exc
        generation_ms = (perf_counter() - generation_started_at) * 1000

        if not completion.choices:
            raise RuntimeError("Model provider returned no choices")

        content = completion.choices[0].message.content

        if not isinstance(content, str):
            raise TypeError("Model provider returned no text content")

        raw_usage = getattr(completion, "usage", None)
        generation_usage = (
            TokenUsage(
                prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
            )
            if raw_usage is not None
            else None
        )

        routing = RoutingInfo(
            complexity=analysis.complexity,
            task_type=analysis.task_type,
            contains_personal_data=analysis.contains_personal_data,
            personal_data_types=analysis.personal_data_types,
            provider=decision.provider,
            reason=decision.reason,
        )

        return ChatResponse(
            model=completion.model,
            content=content,
            provider=decision.provider,
            routing=routing,
            usage=UsageStats(
                analysis=analysis.usage,
                generation=generation_usage,
            ),
            performance=PerformanceStats(
                analysis_ms=analysis_ms,
                generation_ms=generation_ms,
                total_ms=(perf_counter() - total_started_at) * 1000,
            ),
        )
