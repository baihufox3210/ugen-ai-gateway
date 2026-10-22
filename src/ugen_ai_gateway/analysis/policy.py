import re
from typing import Literal

from pydantic import BaseModel

from ugen_ai_gateway.analysis.schemas import RequestAnalysis

ProviderName = Literal["local", "cloud"]
# Words that on their own mark a demanding technical task.
_STRONG_CUES = re.compile(
    r"architecture|architectural|system design|trade[ -]?offs?|step[ -]?by[ -]?step|"
    r"comprehensive|multi[ -]?step|refactor|debug|distributed|scalab|"
    r"high[ -]availability|concurren|"
    r"\b(?:design|build|implement) (?:a|an|the) (?:[\w-]+ ){0,3}(?:system|service|api|"
    r"database|schema|architecture|pipeline|protocol|limiter|cache|queue|platform|"
    r"tree|graph|algorithm|compiler|interpreter|engine)|"
    r"架構|取捨|逐步|多步驟|重構|除錯|完整方案|全面分析|分散式|高可用|高併發|"
    r"設計[^，。、；：！？,.;:!?\n]{0,24}?(?:系統|資料庫|API|架構|演算法|平台)",
    re.IGNORECASE,
)
# Everyday words that also appear in simple requests ("設計一句祝福語",
# "比較一下兩種水果"): they only count with more signal, see below.
_COMMON_CUES = re.compile(
    r"\b(?:designs?|designed|designing|compar(?:e|es|ed|ing|isons?)|"
    r"evaluat(?:e|es|ed|ing|ions?)|analy(?:[sz]e|[sz]es|[sz]ed|[sz]ing|sis|ses)|"
    r"implement(?:s|ed|ing|ations?)?|optimi[sz](?:e|es|ed|ing|ations?)|strateg(?:y|ies))\b|"
    r"設計|比較|評估|規劃|策略|整合|優化",
    re.IGNORECASE,
)
# A common cue counts on a request at least this long, with each CJK
# character counted twice (Chinese says more per character).
_CUE_LENGTH = 120
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


class RoutingDecision(BaseModel):
    """Model routing decision and its rationale."""

    provider: ProviderName
    reason: str
    requires_redaction: bool = False


class RoutingPolicy:
    """Apply privacy-first rules to model routing."""

    def __init__(self, *, cloud_enabled: bool = True) -> None:
        self._cloud_enabled = cloud_enabled

    @property
    def cloud_enabled(self) -> bool:
        return self._cloud_enabled

    def classify_complexity(
        self, user_input: str, analysis: RequestAnalysis
    ) -> RequestAnalysis:
        """Promote complex prompts when the analyzer underestimates their scope."""
        if analysis.complexity != "simple":
            return analysis

        has_multiple_parts = (
            len(re.findall(r"(?:^|\n)\s*(?:\d+[.)、]|[-*])\s+", user_input)) >= 2
        )
        is_long = len(user_input) >= 700
        common_cues = {cue.lower() for cue in _COMMON_CUES.findall(user_input)}
        # A strong cue, or an everyday one backed by length or by a second cue
        # ("比較三種方案並規劃時程"), so short casual requests stay local.
        has_complex_cue = bool(_STRONG_CUES.search(user_input)) or bool(
            common_cues
            and (
                len(common_cues) >= 2
                or len(user_input) + len(_CJK.findall(user_input)) >= _CUE_LENGTH
            )
        )
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
