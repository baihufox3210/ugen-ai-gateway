import asyncio
import json

import pytest

from tests.test_gateway import FakeAnalyzer, analysis, make_service
from ugen_ai_gateway.analysis.complexity import CASES_FILE, assess_complexity
from ugen_ai_gateway.api.schemas import ChatRequest

# Realistic requests (600, in two batches by different authors) labeled by whether a 7-9B local model answers them well
# (simple) or a stronger model should (moderate, complex). Written separately
# from the rules, in Chinese and English, with deliberately tricky cases.
CASES = json.loads(CASES_FILE.read_text("utf-8"))


def test_rules_are_never_wrong_when_they_are_sure() -> None:
    """Routing depends on simple versus not simple; when the rules decide on
    their own they must agree with the label every time."""
    wrong = []
    certain = 0
    for case in CASES:
        result = assess_complexity(case["text"], case["previous"])
        if result.certain:
            certain += 1
            if (result.complexity == "simple") != (case["label"] == "simple"):
                wrong.append((case["text"], result.complexity, case["label"]))
    assert wrong == []
    # The rest goes to the analysis model; the rules still settle a good share.
    assert certain / len(CASES) >= 0.3


def test_rules_only_mode_gets_most_requests_right() -> None:
    wrong = [
        case
        for case in CASES
        if (assess_complexity(case["text"], case["previous"]).complexity == "simple")
        != (case["label"] == "simple")
    ]
    # Rules-only mode guesses on unclear requests; the default asks the model.
    assert len(wrong) / len(CASES) <= 0.2


@pytest.mark.parametrize(
    ("text", "previous", "complexity", "certain"),
    [
        ("你好", None, "simple", True),
        ("幫我設計一句生日祝福", None, "simple", True),
        ("什麼是微服務", None, "simple", True),
        ("What is system design?", None, "simple", True),
        ("幫我設計一個會員系統", None, "complex", True),
        ("證明根號2是無理數", None, "complex", True),
        ("implement an LRU cache", None, "complex", True),
        ("第二點再詳細一點", "幫我設計一個會員系統", "complex", True),
        ("謝謝，很清楚！", "幫我設計一個會員系統", "simple", True),
        # No clear signal: the analysis model decides.
        ("為什麼 TCP 需要三次握手而不是兩次", None, "moderate", False),
        ("solve this", None, "simple", False),
    ],
)
def test_examples(
    text: str, previous: str | None, complexity: str, certain: bool
) -> None:
    result = assess_complexity(text, previous)
    assert (result.complexity, result.certain) == (complexity, certain)


def _ask(service, *texts: str):
    messages = []
    for index, text in enumerate(texts):
        if index:
            messages.append({"role": "assistant", "content": "…"})
        messages.append({"role": "user", "content": text})
    return asyncio.run(service.chat(ChatRequest(messages=messages)))


def test_hybrid_skips_the_model_only_when_the_rules_are_sure() -> None:
    service, _, _, analyzer = make_service(
        analyzer=FakeAnalyzer(analysis(complexity="complex")), analysis_mode="hybrid"
    )

    sure = _ask(service, "謝謝！")
    unsure = _ask(service, "為什麼 TCP 需要三次握手")

    assert analyzer.inputs == ["為什麼 TCP 需要三次握手"]
    assert sure.routing.analysis_source == "rules"
    assert sure.provider == "local"
    # When the rules are unsure, the model's judgement decides.
    assert unsure.routing.complexity == "complex"
    assert unsure.provider == "cloud"


def test_the_rules_overrule_the_model_when_they_are_sure() -> None:
    service, _, _, _ = make_service(
        analyzer=FakeAnalyzer(analysis(complexity="simple")),
        analysis_mode="llm",
    )

    response = _ask(service, "幫我設計一個會員系統")

    assert response.routing.complexity == "complex"
    assert response.provider == "cloud"


def test_an_unclear_follow_up_is_judged_with_the_request_before_it() -> None:
    service, _, _, analyzer = make_service(
        analyzer=FakeAnalyzer(analysis(complexity="complex")), analysis_mode="hybrid"
    )

    _ask(service, "我想開早午餐店，預算 250 萬", "如果預算砍到 150 萬要怎麼改？")

    assert (
        "我想開早午餐店，預算 250 萬\n\n如果預算砍到 150 萬要怎麼改？"
        in analyzer.inputs
    )
