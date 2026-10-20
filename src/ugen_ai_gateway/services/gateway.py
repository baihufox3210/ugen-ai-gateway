from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, cast

from openai.types.chat import ChatCompletionMessageParam

from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer
from ugen_ai_gateway.analysis.pii import redact_personal_data
from ugen_ai_gateway.analysis.policy import RoutingDecision, RoutingPolicy
from ugen_ai_gateway.analysis.schemas import RequestAnalysis, TokenUsage
from ugen_ai_gateway.api.schemas import (
    ChatRequest,
    ChatResponse,
    PerformanceStats,
    RoutingInfo,
    UsageStats,
)
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.providers.base import ModelProvider
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
ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
SUPPORTED_PERSONAL_DATA_TYPES = {
    "email",
    "taiwan_mobile",
    "taiwan_id",
    "credit_card",
}


@dataclass
class GatewayEvent:
    """One step of a chat request: a stage update, a text delta, or the result."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    response: ChatResponse | None = None

    def to_json(self) -> dict[str, Any]:
        if self.response is not None:
            return {"type": self.type, "data": self.response.model_dump(mode="json")}
        return {"type": self.type, **self.data}


@dataclass
class _PreparedChat:
    analysis: RequestAnalysis
    decision: RoutingDecision
    provider: ModelProvider
    model: str
    messages: list[ChatCompletionMessageParam]
    analysis_ms: float


def _token_usage(raw_usage: Any) -> TokenUsage | None:
    if raw_usage is None:
        return None
    return TokenUsage(
        prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
    )


class GatewayService:
    """Analyze requests and route them to the appropriate model provider."""

    def __init__(
        self,
        *,
        settings: Settings,
        local_provider: ModelProvider,
        cloud_provider: ModelProvider | None,
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
            await self._local_provider.health_check()  # type: ignore[attr-defined]
            if self._cloud_provider is not None:
                await self._cloud_provider.health_check()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - readiness reports any provider failure
            return False
        return True

    async def chat(
        self,
        request: ChatRequest,
        progress: ProgressCallback | None = None,
    ) -> ChatResponse:
        """Run a request to completion, reporting stage events to ``progress``."""
        async for event in self.events(request, stream=False):
            if event.response is not None:
                return event.response
            if progress is not None:
                await progress(event.type, event.data)
        raise RuntimeError("Gateway finished without a response")

    async def events(
        self, request: ChatRequest, *, stream: bool
    ) -> AsyncIterator[GatewayEvent]:
        """Yield stage events, text deltas (when streaming) and the final result."""
        total_started_at = perf_counter()
        user_input = self._validated_user_input(request)

        yield GatewayEvent("analysis_started")
        analysis, analysis_ms = await self._analyze(user_input)
        yield GatewayEvent(
            "analysis_completed",
            {
                "complexity": analysis.complexity,
                "task_type": analysis.task_type,
                "contains_personal_data": analysis.contains_personal_data,
                "personal_data_types": analysis.personal_data_types,
            },
        )

        prepared = self._prepare(request, analysis, analysis_ms)
        yield GatewayEvent(
            "routed",
            {
                "provider": prepared.decision.provider,
                "reason": prepared.decision.reason,
                "complexity": analysis.complexity,
            },
        )

        generation_started_at = perf_counter()
        yield GatewayEvent(
            "generation_started",
            {"provider": prepared.decision.provider, "model": prepared.model},
        )

        if stream:
            parts: list[str] = []
            resolved_model = prepared.model
            usage: TokenUsage | None = None
            try:
                async for chunk in prepared.provider.stream(
                    model=prepared.model,
                    messages=prepared.messages,
                    temperature=request.temperature,
                ):
                    resolved_model = getattr(chunk, "model", None) or resolved_model
                    usage = _token_usage(getattr(chunk, "usage", None)) or usage
                    for choice in chunk.choices or []:
                        text = getattr(choice.delta, "content", None)
                        if text:
                            parts.append(text)
                            yield GatewayEvent("delta", {"content": text})
            except TimeoutError as exc:
                raise ProviderTimeout("Model provider timed out") from exc
            except Exception as exc:
                raise ProviderRequestError(
                    f"Model provider request failed: {exc}"
                ) from exc
            content = "".join(parts)
        else:
            try:
                completion = await prepared.provider.complete(
                    model=prepared.model,
                    messages=prepared.messages,
                    temperature=request.temperature,
                )
            except TimeoutError as exc:
                raise ProviderTimeout("Model provider timed out") from exc
            except Exception as exc:
                raise ProviderRequestError(
                    f"Model provider request failed: {exc}"
                ) from exc

            if not completion.choices:
                raise RuntimeError("Model provider returned no choices")
            message_content = completion.choices[0].message.content
            if not isinstance(message_content, str):
                raise TypeError("Model provider returned no text content")
            content = message_content
            resolved_model = completion.model
            usage = _token_usage(getattr(completion, "usage", None))

        generation_ms = (perf_counter() - generation_started_at) * 1000
        response = ChatResponse(
            model=resolved_model,
            content=content,
            provider=prepared.decision.provider,
            routing=RoutingInfo(
                complexity=analysis.complexity,
                task_type=analysis.task_type,
                contains_personal_data=analysis.contains_personal_data,
                personal_data_types=analysis.personal_data_types,
                provider=prepared.decision.provider,
                reason=prepared.decision.reason,
            ),
            usage=UsageStats(analysis=analysis.usage, generation=usage),
            performance=PerformanceStats(
                analysis_ms=analysis_ms,
                generation_ms=generation_ms,
                total_ms=(perf_counter() - total_started_at) * 1000,
            ),
        )
        yield GatewayEvent("result", response=response)

    def _validated_user_input(self, request: ChatRequest) -> str:
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
        return user_input

    async def _analyze(self, user_input: str) -> tuple[RequestAnalysis, float]:
        started_at = perf_counter()
        try:
            analysis = await self._analyzer.analyze(user_input)
        except TimeoutError as exc:
            raise ProviderTimeout("Analysis provider timed out") from exc
        except Exception as exc:
            raise ProviderRequestError(f"Request analysis failed: {exc}") from exc
        analysis_ms = (perf_counter() - started_at) * 1000
        return self._policy.classify_complexity(user_input, analysis), analysis_ms

    def _prepare(
        self, request: ChatRequest, analysis: RequestAnalysis, analysis_ms: float
    ) -> _PreparedChat:
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
        if request.provider_selection != "auto":
            decision = RoutingDecision(
                provider=request.provider_selection,
                reason=(
                    "Local model selected manually."
                    if request.provider_selection == "local"
                    else "Cloud model selected manually."
                ),
            )

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

        return _PreparedChat(
            analysis=analysis,
            decision=decision,
            provider=provider,
            model=model,
            messages=self._redacted_messages(request, analysis),
            analysis_ms=analysis_ms,
        )

    @staticmethod
    def _redacted_messages(
        request: ChatRequest, analysis: RequestAnalysis
    ) -> list[ChatCompletionMessageParam]:
        messages: list[ChatCompletionMessageParam] = []
        for message in request.messages:
            redacted_message = cast(dict[str, Any], message).copy()
            content = redacted_message.get("content")
            if isinstance(content, str):
                redacted_message["content"] = redact_personal_data(
                    content, additional_values=analysis.personal_data_values
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
        return messages
