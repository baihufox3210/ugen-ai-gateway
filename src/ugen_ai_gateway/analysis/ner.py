"""Optional named-entity detection (people, places) through Microsoft Presidio.

Install with ``uv sync --extra ner`` plus a spaCy model for your language, for
example ``uv run python -m spacy download zh_core_web_sm``.
"""

from typing import Any

from ugen_ai_gateway.analysis.pii import Finder, PersonalDataMatch
from ugen_ai_gateway.services.errors import GatewayConfigurationError

# Presidio entity -> (personal-data type, placeholder label)
ENTITY_TYPES: dict[str, tuple[str, str]] = {
    "PERSON": ("person_name", "PERSON_NAME"),
    "LOCATION": ("location", "LOCATION"),
}


def build_presidio_finder(
    *, language: str, spacy_model: str, score_threshold: float
) -> Finder:
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError as exc:
        raise GatewayConfigurationError(
            "PII_NER=presidio needs the optional dependency: uv sync --extra ner"
        ) from exc

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": language, "model_name": spacy_model}],
        }
    ).create_engine()
    engine: Any = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[language])

    def find(text: str) -> list[PersonalDataMatch]:
        results = engine.analyze(
            text=text,
            language=language,
            entities=list(ENTITY_TYPES),
            score_threshold=score_threshold,
        )
        matches = []
        for result in results:
            data_type, label = ENTITY_TYPES[result.entity_type]
            value = text[result.start : result.end]
            if len(value.strip()) >= 2:
                matches.append(
                    PersonalDataMatch(data_type, label, result.start, result.end, value)
                )
        return matches

    return find
