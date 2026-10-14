from typing import Literal

from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field

from ugen_ai_gateway.analysis.schemas import TaskType


class RoutingInfo(BaseModel):
    complexity: Literal["simple", "moderate", "complex"]
    task_type: TaskType
    contains_personal_data: bool
    personal_data_types: list[str] = Field(default_factory=list)
    provider: Literal["local", "cloud"]
    reason: str


class ChatRequest(BaseModel):
    provider: Literal["local", "cloud"] = "local"
    model: str | None = None
    messages: list[ChatCompletionMessageParam] = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class ChatResponse(BaseModel):
    model: str
    content: str
    provider: str
    routing: RoutingInfo
