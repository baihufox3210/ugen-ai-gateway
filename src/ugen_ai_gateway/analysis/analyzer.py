import json

from pydantic import ValidationError

from ugen_ai_gateway.analysis.pii import (
    detect_personal_data,
    redact_personal_data,
)
from ugen_ai_gateway.analysis.prompts import REQUEST_ANALYSIS_SYSTEM_PROMPT
from ugen_ai_gateway.analysis.schemas import RequestAnalysis, TokenUsage
from ugen_ai_gateway.providers.base import ModelProvider


class RequestAnalyzer:
    """Analyze privacy risks and task complexity using a local model."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
    ) -> None:
        self._provider = provider
        self._model = model

    async def analyze(self, user_input: str) -> RequestAnalysis:
        completion = await self._provider.complete(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": REQUEST_ANALYSIS_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": redact_personal_data(user_input),
                },
            ],
            temperature=0.0,
        )

        if not completion.choices:
            raise RuntimeError("Analysis model returned no choices")

        content = completion.choices[0].message.content

        if not isinstance(content, str):
            raise TypeError("Analysis model returned no text content")

        try:
            payload = json.loads(content)
            analysis = RequestAnalysis.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
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
