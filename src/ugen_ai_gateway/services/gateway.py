from openai.types.chat import ChatCompletionMessageParam

from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer
from ugen_ai_gateway.analysis.policy import RoutingPolicy
from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.providers.cloud import CloudProvider
from ugen_ai_gateway.providers.local import LocalProvider


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

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if not self._settings.local_model:
            raise ValueError("LOCAL_MODEL is not configured")

        user_messages: list[str] = []
        for message in request.messages:
            if message["role"] != "user":
                continue

            content = message.get("content")
            if isinstance(content, str):
                user_messages.append(content)

        user_input = "\n".join(user_messages)

        if not user_input:
            raise ValueError("A text user message is required")

        analysis = await self._analyzer.analyze(user_input)
        decision = self._policy.decide(analysis)

        if decision.provider == "cloud":
            if self._cloud_provider is None:
                raise RuntimeError("Cloud provider is unavailable")

            provider = self._cloud_provider
            model = request.model or self._settings.cloud_model
        else:
            provider = self._local_provider
            model = request.model or self._settings.local_model

        if not model:
            raise ValueError(
                f"No model configured for the {decision.provider} provider"
            )

        messages: list[ChatCompletionMessageParam] = request.messages

        completion = await provider.complete(
            model=model,
            messages=messages,
            temperature=request.temperature,
        )

        if not completion.choices:
            raise RuntimeError("Model provider returned no choices")

        content = completion.choices[0].message.content

        if not isinstance(content, str):
            raise TypeError("Model provider returned no text content")

        return ChatResponse(
            model=completion.model,
            content=content,
            provider=decision.provider,
        )
