import json
import logging
from typing import Any

from pydantic import ValidationError

from ugen_ai_gateway.analysis.pii import (
    detect_personal_data,
    redact_personal_data,
)
from ugen_ai_gateway.analysis.prompts import REQUEST_ANALYSIS_SYSTEM_PROMPT
from ugen_ai_gateway.analysis.schemas import RequestAnalysis, TokenUsage
from ugen_ai_gateway.analysis.thinking import strip_thinking
from ugen_ai_gateway.providers.base import ModelProvider
from ugen_ai_gateway.providers.reasoning import Reasoning, request_options

logger = logging.getLogger(__name__)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """The analysis object in a model's reply: the whole reply, or the first
    JSON object in it (after thinking text or around a ```json fence)."""
    decoder = json.JSONDecoder()
    for start in (i for i, char in enumerate(text) if char == "{"):
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "complexity" in value:
            return value
    return None


class RequestAnalyzer:
    """Analyze privacy risks and task complexity using a local model."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        reasoning: Reasoning | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._reasoning = reasoning or Reasoning()
        self._max_tokens = max_tokens

    async def analyze(self, user_input: str) -> RequestAnalysis:
        # The analysis step never needs to think; a model told how to turn
        # thinking off is asked to, which also makes routing much faster.
        options, messages = request_options(
            self._reasoning,
            [
                {"role": "system", "content": REQUEST_ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": redact_personal_data(user_input)},
            ],
            analysis=True,
        )
        if self._max_tokens:
            options["max_tokens"] = self._max_tokens
        completion = await self._provider.complete(
            model=self._model, messages=messages, temperature=0.0, **options
        )

        if not completion.choices:
            raise RuntimeError("Analysis model returned no choices")

        message = completion.choices[0].message
        content = message.content

        if not isinstance(content, str):
            raise TypeError("Analysis model returned no text content")

        # Thinking text first, then the answer; a server that splits them
        # puts the thinking in reasoning_content and may leave the answer empty.
        payload = extract_json_object(strip_thinking(content)) or extract_json_object(
            content
        )
        if payload is None:
            spare = getattr(message, "reasoning_content", None) or getattr(
                message, "reasoning", None
            )
            if isinstance(spare, str):
                payload = extract_json_object(spare)
        try:
            if payload is None:
                raise ValueError("no analysis object in the reply")
            analysis = RequestAnalysis.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            logger.warning(
                "The analysis model's reply could not be used (%s). It began: %r. "
                "A model that thinks first may need thinking turned off for the "
                "analysis step, or a larger ANALYSIS_MAX_TOKENS.",
                exc,
                content[:200],
            )
            raise ValueError(
                "Analysis model returned an invalid analysis result"
            ) from exc

        detected_types = detect_personal_data(user_input)
        reported_values = list(
            dict.fromkeys(
                value.strip()
                for value in analysis.personal_data_values
                if value.strip() and value in user_input
            )
        )
        personal_data_types = list(
            dict.fromkeys([*analysis.personal_data_types, *detected_types])
        )
        usage = getattr(completion, "usage", None)
        token_usage = (
            TokenUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
            )
            if usage is not None
            else None
        )

        return analysis.model_copy(
            update={
                "contains_personal_data": bool(
                    analysis.contains_personal_data or detected_types or reported_values
                ),
                "personal_data_types": personal_data_types,
                "personal_data_values": reported_values,
                "usage": token_usage,
            }
        )
