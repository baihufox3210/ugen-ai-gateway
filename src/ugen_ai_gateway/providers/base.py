from typing import Protocol

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


class ModelProvider(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float = 0.2,
    ) -> ChatCompletion:
        """Generate a chat completion using the specified model and messages."""
        ...
