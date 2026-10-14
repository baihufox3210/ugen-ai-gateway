import json

from pydantic import ValidationError

from ugen_ai_gateway.analysis.prompts import REQUEST_ANALYSIS_SYSTEM_PROMPT
from ugen_ai_gateway.analysis.schemas import RequestAnalysis
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
                    "content": user_input,
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
            return RequestAnalysis.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(
                "Analysis model returned an invalid analysis result"
            ) from exc
