from typing import Literal

from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    provider: Literal["local", "cloud"] = "local"
    model: str | None = None
    messages: list[ChatCompletionMessageParam] = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class ChatResponse(BaseModel):
    model: str
    content: str
    provider: str
