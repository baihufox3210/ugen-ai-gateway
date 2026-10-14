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


class RequestAnalysis(BaseModel):
    """Structured analysis result for a user request."""

    contains_personal_data: bool = Field(
        description="Whether the request contains personal information."
    )

    personal_data_types: list[str] = Field(
        default_factory=list, description="Types of personal information detected."
    )

    complexity: TaskComplexity = Field(description="Estimated complexity of the task.")

    task_type: TaskType = Field(description="Primary category of the task.")

    reason: str = Field(description="Brief explanation for the analysis.")
