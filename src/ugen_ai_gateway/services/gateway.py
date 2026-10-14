from typing import Literal

from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.providers.cloud import CloudProvider
from ugen_ai_gateway.providers.local import LocalProvider


class GatewayService:
    """Orchestrate requests to local and cloud model providers."""

    def __init__(
        self,
        *,
        settings: Settings,
        local_provider: LocalProvider,
        cloud_provider: CloudProvider,
    ) -> None:
        self._settings = settings
        self._local_provider = local_provider
        self._cloud_provider = cloud_provider

    async def chat(self, request: ChatRequest) -> ChatResponse:
        provider_name: Literal["local", "cloud"] = request.provider

        if provider_name == "local":
            provider = self._local_provider
            model = request.model or self._settings.local_model
        else:
            if not self._settings.cloud_api_key:
                raise ValueError("Cloud provider is not configured")

            provider = self._cloud_provider
            model = request.model or self._settings.cloud_model

        if not model:
            raise ValueError(f"No model configured for the {provider_name} provider")

        completion = await provider.complete(
            model=model,
            messages=request.messages,
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
            provider=provider_name,
        )
