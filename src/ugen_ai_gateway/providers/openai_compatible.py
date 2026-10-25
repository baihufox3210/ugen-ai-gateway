from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
)

from ugen_ai_gateway.providers.base import Locality


class OpenAICompatibleProvider:
    """Provider for any OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, client: AsyncOpenAI, *, name: str, locality: Locality) -> None:
        self._client = client
        self.name = name
        self.locality: Locality = locality
        # Models that rejected a temperature (some accept only their default).
        self._no_temperature: set[str] = set()

    async def _create(self, model: str, temperature: float, **kwargs: Any) -> Any:
        """Create a completion; if the model refuses the temperature, retry
        without it and remember that for this model."""
        if model not in self._no_temperature:
            try:
                return await self._client.chat.completions.create(
                    model=model, temperature=temperature, **kwargs
                )
            except openai.BadRequestError as exc:
                if "temperature" not in str(exc).lower():
                    raise
                self._no_temperature.add(model)
        return await self._client.chat.completions.create(model=model, **kwargs)

    async def health_check(self) -> None:
        await self._client.models.list()

    async def embed(self, *, model: str, input: Any, **options: Any) -> Any:
        return await self._client.embeddings.create(model=model, input=input, **options)

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.2,
        **options: Any,
    ) -> ChatCompletion:
        return await self._create(model, temperature, messages=messages, **options)

    async def stream(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.2,
        **options: Any,
    ) -> AsyncIterator[ChatCompletionChunk]:
        response = await self._create(
            model,
            temperature,
            messages=messages,
            **options,
            stream=True,
            stream_options={"include_usage": True},
        )
        try:
            async for chunk in response:
                yield chunk
        finally:
            await response.close()
