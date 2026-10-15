from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
)


class LocalProvider:
    """Provider for a local OpenAI-compatible API."""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def health_check(self) -> None:
        await self._client.models.list()

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.2,
    ) -> ChatCompletion:
        return await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
