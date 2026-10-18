import re
from typing import Literal

from pydantic import BaseModel

from ugen_ai_gateway.analysis.schemas import RequestAnalysis

ProviderName = Literal["local", "cloud"]
_COMPLEXITY_CUES = re.compile(
    r"architecture|architectural|system design|design a|trade[ -]?offs?|compare|"
    r"comparison|evaluate|analy[sz]e|step[ -]?by[ -]?step|comprehensive|"
    r"multi[ -]?step|implement|refactor|debug|optimi[sz]e|strategy|"
    r"架構|設計|比較|取捨|評估|規劃|策略|逐步|多步驟|整合|重構|除錯|優化|完整方案|全面分析",
    re.IGNORECASE,
)


class RoutingDecision(BaseModel):
    """Model routing decision and its rationale."""

    provider: ProviderName
    reason: str
    requires_redaction: bool = False


class RoutingPolicy:
    """Apply privacy-first rules to model routing."""

    def __init__(self, *, cloud_enabled: bool = True) -> None:
        self._cloud_enabled = cloud_enabled

    def classify_complexity(self, user_input: str, analysis: RequestAnalysis) -> RequestAnalysis:
        """Promote complex prompts when the analyzer underestimates their scope."""
        if analysis.complexity != "simple":
            return analysis

        has_complex_cue = bool(_COMPLEXITY_CUES.search(user_input))
        has_multiple_parts = len(
            re.findall(r"(?:^|\n)\s*(?:\d+[.)、]|[-*])\s+", user_input)
        ) >= 2
        is_long = len(user_input) >= 700
        if not (has_complex_cue or has_multiple_parts or is_long):
            return analysis

        reason = "Complexity signals in the request require multi-step reasoning."
        return analysis.model_copy(update={"complexity": "complex", "reason": reason})

    def decide(self, analysis: RequestAnalysis) -> RoutingDecision:
        if analysis.complexity in {"moderate", "complex"} and self._cloud_enabled:
            return RoutingDecision(
                provider="cloud",
                reason="Moderate or complex task; cloud model is enabled.",
            )

        return RoutingDecision(
            provider="local",
            reason="Local model selected by the default routing policy.",
        )
