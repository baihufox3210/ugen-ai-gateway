import asyncio
import json
from types import SimpleNamespace
from typing import Any

from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer


class FakeProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.payload))
                )
            ]
        )


def test_analyzer_marks_regex_detected_pii_even_when_model_misses_it() -> None:
    provider = FakeProvider(
        {
            "contains_personal_data": False,
            "personal_data_types": ["name"],
            "complexity": "simple",
            "task_type": "general",
            "reason": "A simple request.",
        }
    )
    analyzer = RequestAnalyzer(provider=provider, model="local-model")  # type: ignore[arg-type]

    result = asyncio.run(analyzer.analyze("Contact alex@example.com or 0912-345-678"))

    assert result.contains_personal_data is True
    assert result.personal_data_types == ["name", "email", "taiwan_mobile"]
    assert provider.calls[0]["model"] == "local-model"
    assert provider.calls[0]["messages"][1]["content"] == (
        "Contact alex@example.com or 0912-345-678"
    )


def test_analyzer_preserves_model_analysis_when_no_patterns_match() -> None:
    payload = {
        "contains_personal_data": False,
        "personal_data_types": [],
        "complexity": "moderate",
        "task_type": "reasoning",
        "reason": "Requires several steps.",
    }
    analyzer = RequestAnalyzer(
        provider=FakeProvider(payload),  # type: ignore[arg-type]
        model="local-model",
    )

    result = asyncio.run(analyzer.analyze("Explain how this algorithm works."))

    assert result.model_dump() == payload
