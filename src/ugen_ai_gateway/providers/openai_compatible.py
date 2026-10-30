import logging
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
from ugen_ai_gateway.providers.reasoning import Thinking, ways

logger = logging.getLogger(__name__)

# What a server says when it does not take a thinking field.
_NOT_TAKEN = (
    "chat_template",
    "extra_body",
    "enable_thinking",
    "reasoning_effort",
    "unexpected keyword",
    "unrecognized",
    "unknown",
    "unsupported",
    "not supported",
    "extra inputs",
    "additional properties",
    "not permitted",
)


def _rejected_thinking(exc: Exception) -> bool:
    """Whether ``exc`` is a server refusing the thinking field: a refused
    request, or a proxy's 5xx that names the field."""
    if not isinstance(exc, openai.APIStatusError):
        return False
    if exc.status_code in (400, 422):
        return True
    return exc.status_code in (500, 501) and any(
        w in str(exc).lower() for w in _NOT_TAKEN
    )


class OpenAICompatibleProvider:
    """Provider for any OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, client: AsyncOpenAI, *, name: str, locality: Locality) -> None:
        self._client = client
        self.name = name
        self.locality: Locality = locality
        # Models that rejected a temperature (some accept only their default).
        self._no_temperature: set[str] = set()
        # The way of asking for thinking that worked, per model; "" when none did.
        self._thinking_way: dict[str, str] = {}

    async def _create(
        self,
        model: str,
        temperature: float,
        thinking: Thinking | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create a completion, asking for the thinking level wanted in the way
        this model's server takes it, and remembering which way that is."""
        if thinking is not None and self._thinking_way.get(model) != "":
            tried = self._thinking_way.get(model)
            candidates = ways(thinking, local=self.locality == "local")
            if tried:
                candidates = [way for way in candidates if way.name == tried]
            last: Exception | None = None
            for way in candidates:
                attempt = {**kwargs, **way.fields}
                attempt["messages"] = way.messages(kwargs["messages"])
                try:
                    result = await self._create_plain(model, temperature, **attempt)
                except Exception as exc:
                    if not _rejected_thinking(exc):
                        raise
                    last = exc
                    continue
                self._thinking_way[model] = way.name
                return result
            self._thinking_way[model] = ""
            logger.warning(
                "Thinking cannot be controlled for %s: the server refused every way "
                "tried (%s); the last said: %s. Requests go without it.",
                model,
                ", ".join(way.name for way in candidates),
                str(last)[:300],
            )
        return await self._create_plain(model, temperature, **kwargs)

    async def _create_plain(self, model: str, temperature: float, **kwargs: Any) -> Any:
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
        thinking: Thinking | None = None,
        **options: Any,
    ) -> ChatCompletion:
        return await self._create(
            model, temperature, thinking, messages=messages, **options
        )

    async def stream(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.2,
        thinking: Thinking | None = None,
        **options: Any,
    ) -> AsyncIterator[ChatCompletionChunk]:
        response = await self._create(
            model,
            temperature,
            thinking,
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
