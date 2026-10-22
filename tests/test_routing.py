import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.test_gateway import FakeAnalyzer, FakeProvider
from ugen_ai_gateway.analysis.policy import RoutingPolicy
from ugen_ai_gateway.analysis.schemas import RequestAnalysis
from ugen_ai_gateway.api.schemas import ChatRequest
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.dependencies import _service_from_routing_config
from ugen_ai_gateway.routing.config import RoutingConfig, load_routing_config
from ugen_ai_gateway.routing.engine import RuleEngine
from ugen_ai_gateway.services.errors import ModelNotFound, ProviderUnavailable
from ugen_ai_gateway.services.gateway import GatewayService

EXAMPLE = Path(__file__).parents[1] / "routing.example.yaml"


def config(**overrides: Any) -> RoutingConfig:
    data: dict[str, Any] = {
        "providers": [
            {"name": "ollama", "locality": "local", "models": [{"id": "qwen"}]},
            {
                "name": "gemini",
                "locality": "cloud",
                "api_key": "k",
                "models": [
                    {"id": "flash", "output_cost_per_million": 2.5},
                    {"id": "pro"},
                ],
            },
        ],
        "defaults": {"local": {"provider": "ollama"}, "cloud": {"provider": "gemini"}},
        "analysis": {"provider": "ollama"},
        "privacy": {"never_cloud": ["taiwan_id"]},
        "rules": [
            {
                "name": "code",
                "when": {"task_type": ["coding"], "complexity": ["complex"]},
                "route": {"provider": "gemini", "model": "pro"},
            },
            {
                "name": "long",
                "when": {"min_chars": 100},
                "route": {"provider": "gemini"},
            },
        ],
    }
    data.update(overrides)
    return RoutingConfig.model_validate(data)


def analysis(**fields: Any) -> RequestAnalysis:
    values: dict[str, Any] = {
        "contains_personal_data": False,
        "complexity": "simple",
        "task_type": "general",
        "reason": "t",
    }
    values.update(fields)
    return RequestAnalysis(**values)


def test_example_file_is_valid() -> None:
    loaded = load_routing_config(EXAMPLE)

    assert loaded.rules[0].name == "hard-coding-to-pro"
    assert loaded.find_model("gemini-2.5-pro")[0].name == "gemini"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"analysis": {"provider": "gemini"}}, "analysis must use a local provider"),
        ({"defaults": {"local": {"provider": "gemini"}}}, "defaults.local must use"),
        (
            {"rules": [{"name": "x", "route": {"provider": "nope"}}]},
            "unknown provider 'nope'",
        ),
        (
            {
                "rules": [
                    {"name": "x", "route": {"provider": "gemini", "model": "ultra"}}
                ]
            },
            "has no model 'ultra'",
        ),
    ],
)
def test_invalid_configs_are_rejected(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        config(**override)


def test_duplicate_provider_names_are_rejected() -> None:
    provider = {"name": "a", "locality": "local", "models": [{"id": "m"}]}
    with pytest.raises(ValidationError, match="unique"):
        config(providers=[provider, provider])


def test_first_matching_rule_wins_and_unmatched_requests_stay_local() -> None:
    engine = RuleEngine(config())

    code = engine.decide(analysis(task_type="coding", complexity="complex"), chars=500)
    long = engine.decide(analysis(), chars=500)
    short = engine.decide(analysis(), chars=10)

    assert (code.provider.name, code.model.id, code.rule) == ("gemini", "pro", "code")
    assert (long.model.id, long.rule) == ("flash", "long")
    assert (short.provider.name, short.rule) == ("ollama", None)


@pytest.mark.parametrize("selection", ["auto", "cloud"])
def test_never_cloud_overrides_rules_and_explicit_choices(selection: str) -> None:
    engine = RuleEngine(config())
    sensitive = analysis(
        contains_personal_data=True,
        personal_data_types=["taiwan_id"],
        task_type="coding",
        complexity="complex",
    )

    route = engine.decide(sensitive, selection=selection)  # type: ignore[arg-type]

    assert route.locality == "local"
    assert route.rule == "privacy.never_cloud"
    assert "taiwan_id" in route.reason


def test_pinned_model_and_unknown_model_override() -> None:
    engine = RuleEngine(config())

    pinned = engine.decide(analysis(), model="pro")

    assert (pinned.provider.name, pinned.model.id) == ("gemini", "pro")
    # An unknown model would run unpriced, escaping budgets.
    with pytest.raises(ModelNotFound):
        engine.decide(analysis(), model="custom-tag")


def test_unavailable_providers_are_skipped() -> None:
    engine = RuleEngine(config(), available={"ollama"})

    assert engine.decide(analysis(), chars=500).provider.name == "ollama"
    assert engine.default("cloud") is None
    assert engine.models() == ["qwen"]
    with pytest.raises(ProviderUnavailable):
        engine.decide(analysis(), selection="cloud")


def test_gateway_uses_routing_config_with_several_models() -> None:
    providers = {"ollama": FakeProvider(), "gemini": FakeProvider()}
    analyzer = FakeAnalyzer(analysis(task_type="coding", complexity="complex"))
    service = GatewayService(
        settings=Settings(analysis_mode="llm"),
        local_provider=providers["ollama"],  # type: ignore[arg-type]
        cloud_provider=None,
        analyzer=analyzer,  # type: ignore[arg-type]
        policy=RoutingPolicy(),
        routing=config(),
        providers=providers,  # type: ignore[arg-type]
    )

    response = asyncio.run(
        service.chat(ChatRequest(messages=[{"role": "user", "content": "fix bug"}]))
    )

    assert providers["gemini"].calls[0]["model"] == "pro"
    assert response.provider == "cloud"
    assert response.routing.provider_name == "gemini"
    assert response.routing.rule == "code"
    assert providers["ollama"].calls == []


def test_providers_without_api_keys_are_disabled(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    service = _service_from_routing_config(Settings(), load_routing_config(EXAMPLE))

    assert service.engine.available == {"ollama"}
    assert service.engine.default("cloud") is None

    monkeypatch.setenv("GEMINI_API_KEY", "set")
    service = _service_from_routing_config(Settings(), load_routing_config(EXAMPLE))
    assert service.engine.available == {"ollama", "gemini"}


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        # Everyday requests with a common cue word stay simple (and local).
        ("幫我設計一句生日卡片的祝福語", "simple"),
        ("比較一下蘋果和香蕉哪個好吃", "simple"),
        ("優化這句話", "simple"),
        ("Design a birthday card message", "simple"),
        ("Compare A and B", "simple"),
        ("幫我設計一句祝福語，送給系統管理員", "simple"),
        ("I am a designer; compare apples and pears", "simple"),
        # Strong cues, two cues, or a long request are promoted.
        (
            "Compare microservice and monolith architecture for a 50-person team",
            "complex",
        ),
        ("Design a rate limiter", "complex"),
        ("幫我設計一個會員系統", "complex"),
        ("Design a scalable distributed system", "complex"),
        ("Implement a red-black tree in Rust with deletion", "complex"),
        ("幫我設計一個支援百萬用戶同時在線的即時聊天系統", "complex"),
        ("設計一個高可用的分散式訊息佇列系統", "complex"),
        ("幫我重構這段程式", "complex"),
        ("比較三種補償方案並規劃時程", "complex"),
        (
            (
                "請幫我評估我們公司導入雲端 ERP 的風險、成本與時程，並依照部門需求提出"
                "分階段導入的建議，列出每階段的預算與負責人，以及需要注意的資安與個資合規事項"
            ),
            "complex",
        ),
    ],
)
def test_complexity_cues_need_enough_signal(prompt: str, expected: str) -> None:
    from ugen_ai_gateway.analysis.policy import RoutingPolicy
    from ugen_ai_gateway.analysis.rules import rule_analysis

    result = RoutingPolicy().classify_complexity(prompt, rule_analysis(prompt))

    assert result.complexity == expected
