from typing import Literal

from pydantic import BaseModel, Field

TaskComplexity = Literal["simple", "moderate", "complex"]

TaskType = Literal[
    "general",
    "coding",
    "reasoning",
    "summarization",
    "translation",
    "other",
]


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RequestAnalysis(BaseModel):
    """Structured analysis result for a user request."""

    contains_personal_data: bool = Field(
        description="Whether the request contains personal information."
    )

    personal_data_types: list[str] = Field(
        default_factory=list, description="Types of personal information detected."
    )

    personal_data_values: list[str] = Field(
        default_factory=list,
        description="Exact personal data values that should be redacted.",
    )

    complexity: TaskComplexity = Field(description="Estimated complexity of the task.")

    task_type: TaskType = Field(description="Primary category of the task.")

    reason: str = Field(description="Brief explanation for the analysis.")

    usage: TokenUsage | None = None
