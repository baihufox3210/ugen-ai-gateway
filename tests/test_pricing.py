import asyncio
from typing import Any

import pytest

from tests.test_gateway import FakeAnalyzer, FakeProvider
from tests.test_routing import config
from ugen_ai_gateway.analysis.policy import RoutingPolicy
from ugen_ai_gateway.api.schemas import ChatRequest
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.services.gateway import GatewayService
from ugen_ai_gateway.services.pricing import PriceBook, catalog, normalize
from ugen_ai_gateway.services.usage import UsageStore


@pytest.mark.parametrize(
    "name",
    [
        "claude-haiku-5-5",
        "anthropic/claude-haiku-5-5",
        "openrouter/anthropic/claude-haiku-5-5",
        "claude-haiku-5-5-20260101",
        "claude-haiku-5.5",
        "CLAUDE-HAIKU-5-5",
        "us.anthropic.claude-haiku-5-5-v1:0",
        "vertex_ai/claude-haiku-5-5@20260101",
    ],
)
def test_proxy_spellings_find_the_same_price(name: str) -> None:
    price = PriceBook().lookup(name)

    assert (price.matched, price.input, price.output) == (
        "claude-haiku-5-5",
        0.10,
        0.50,
    )
    assert price.source == "catalog" and price.confidence >= 0.95


def test_words_in_another_order_and_dated_previews_match() -> None:
    book = PriceBook()

    assert book.lookup("claude-3-5-haiku").matched == "claude-haiku-3-5"
    assert (
        book.lookup("gemini-2.5-flash-lite-preview-06-17").matched
        == "gemini-2.5-flash-lite"
    )
    assert book.lookup("o4-mini-2025-04-16").matched == "o4-mini"


def test_a_small_typo_is_a_fuzzy_match_with_lower_confidence() -> None:
    price = PriceBook().lookup("claud-haiku-5-5")

    assert price.matched == "claude-haiku-5-5" and price.method == "fuzzy"
    assert price.label == "catalog-fuzzy" and price.confidence < 0.95


@pytest.mark.parametrize(
    "name",
    [
        "gpt-5-turbo",
        "claude-haiku-latest",
        "gpt-5.6",
        "my-finetune",
        "gemini-flash",
        "claude-sonnet-4-7",
    ],
)
def test_similar_but_different_models_are_not_matched(name: str) -> None:
    assert not PriceBook().lookup(name).known


def test_versions_and_sizes_are_never_blurred() -> None:
    book = PriceBook()

    assert book.lookup("gpt-5-mini").matched == "gpt-5-mini"
    assert book.lookup("gpt-5-nano").matched == "gpt-5-nano"
    assert book.lookup("gpt-5").matched == "gpt-5"
    assert book.lookup("gpt-5.4-mini").matched == "gpt-5.4-mini"
    assert book.lookup("gemini-2.5-flash").matched == "gemini-2.5-flash"
    assert book.lookup("claude-sonnet-4").matched == "claude-sonnet-4"
    assert book.lookup("claude-sonnet-4-5").matched == "claude-sonnet-4-5"


def test_the_built_in_list_covers_the_main_vendors_and_is_well_formed() -> None:
    entries = catalog()
    providers = {entry["provider"] for entry in entries}

    assert {"Anthropic", "OpenAI", "Google", "DeepSeek"} <= providers
    assert len(entries) >= 40
    ids = [normalize(entry["id"]) for entry in entries]
    assert len(set(ids)) == len(ids)
    for entry in entries:
        assert entry["input"] >= 0 and entry["output"] >= entry["input"] * 0
        assert entry["status"] in {"current", "legacy", "retired", "limited"}


def test_a_price_typed_for_a_model_beats_the_lists_and_custom_beats_built_in() -> None:
    book = PriceBook([{"id": "claude-haiku-5-5", "input": 1.0, "output": 2.0}])

    assert book.lookup("anthropic/claude-haiku-5-5").source == "custom"
    typed = book.resolve(
        ["claude-haiku-5-5"], local=False, configured_input=7, configured_output=9
    )
    assert (typed.source, typed.input, typed.output) == ("configured", 7, 9)
    local = book.resolve(["qwen3"], local=True)
    assert (local.source, local.input) == ("local", 0)
    assert not book.resolve(["my-private-model"], local=False).known


def _service(
    cloud_model: str, usage: UsageStore, custom: list | None = None
) -> GatewayService:
    local, cloud = FakeProvider(), FakeProvider()
    cloud.completion.usage = type(
        "U",
        (),
        {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "total_tokens": 0},
    )()
    routing = config(
        providers=[
            {"name": "ollama", "locality": "local", "models": [{"id": "qwen"}]},
            {
                "name": "proxy",
                "locality": "cloud",
                "api_key": "k",
                "models": [{"id": cloud_model}],
            },
        ],
        defaults={"local": {"provider": "ollama"}, "cloud": {"provider": "proxy"}},
        rules=[],
    )
    service = GatewayService(
        settings=Settings(analysis_mode="llm"),
        local_provider=local,  # type: ignore[arg-type]
        cloud_provider=None,
        analyzer=FakeAnalyzer(),  # type: ignore[arg-type]
        policy=RoutingPolicy(),
        routing=routing,
        providers={"ollama": local, "proxy": cloud},  # type: ignore[dict-item]
        usage_store=usage,
    )
    if custom is not None:
        service._prices = PriceBook(custom)
    return service


def _ask(service: GatewayService) -> None:
    asyncio.run(
        service.chat(
            ChatRequest(
                provider_selection="cloud", messages=[{"role": "user", "content": "hi"}]
            )
        )
    )


def test_a_proxied_model_name_is_priced_from_the_list() -> None:
    usage = UsageStore(":memory:")
    service = _service("anthropic/claude-haiku-5-5-20260101", usage)

    _ask(service)

    row = asyncio.run(usage.recent(limit=1))[0]
    assert row["cost_usd"] == pytest.approx(0.10 + 0.50)
    assert (row["price_source"], row["price_match"]) == ("catalog", "claude-haiku-5-5")


def test_a_model_with_no_known_price_claims_no_saving_and_is_reported() -> None:
    usage = UsageStore(":memory:")
    service = _service("acme-secret-model", usage)

    _ask(service)

    row = asyncio.run(usage.recent(limit=1))[0]
    assert (row["price_source"], row["cost_usd"], row["baseline_cost_usd"]) == (
        "unknown",
        0,
        0,
    )
    stats = asyncio.run(usage.stats(days=1))
    assert stats["savings_usd"] == 0
    assert stats["pricing"]["unknown"] == [
        {"model": "acme-secret-model", "requests": 1}
    ]
    # With the model's own price added, it is priced and no longer reported.
    priced = _service(
        "acme-secret-model",
        UsageStore(":memory:"),
        [{"id": "acme-secret-model", "input": 2, "output": 4}],
    )
    _ask(priced)
    assert asyncio.run(priced._usage_store.recent(limit=1))[0][
        "cost_usd"
    ] == pytest.approx(6)


def test_a_fuzzy_match_is_flagged_in_the_stats() -> None:
    usage = UsageStore(":memory:")
    _ask(_service("claud-haiku-5-5", usage))

    stats = asyncio.run(usage.stats(days=1))

    assert stats["pricing"]["fuzzy"] == [
        {"model": "claud-haiku-5-5", "matched": "claude-haiku-5-5", "requests": 1}
    ]
    assert stats["pricing"]["unknown"] == []


@pytest.fixture
def api(monkeypatch) -> Any:
    from fastapi.testclient import TestClient

    from ugen_ai_gateway.api import access
    from ugen_ai_gateway.dependencies import get_gateway_service, get_usage_store
    from ugen_ai_gateway.main import create_app

    monkeypatch.setattr(
        "ugen_ai_gateway.api.access.get_settings", lambda: Settings(gateway_api_key="")
    )
    usage = UsageStore(":memory:")
    app = create_app()
    app.dependency_overrides[get_usage_store] = lambda: usage
    app.dependency_overrides[get_gateway_service] = lambda: None
    monkeypatch.setattr(
        "ugen_ai_gateway.api.pricing_routes.get_gateway_service", lambda: None
    )
    monkeypatch.setattr(
        "ugen_ai_gateway.api.pricing_routes.models_changed", lambda: None
    )
    del access
    return TestClient(app), usage


def test_the_price_list_can_be_read_corrected_and_checked(api) -> None:
    client, usage = api

    shown = client.get("/v1/ugen/pricing").json()
    assert shown["custom"] == [] and len(shown["catalog"]) >= 40 and shown["unit"]

    saved = client.put(
        "/v1/ugen/pricing",
        json={
            "custom": [
                {"id": "acme/my-model", "input": 1.5, "output": 3, "note": "contract"}
            ]
        },
    )
    assert saved.json()["custom"] == [
        {"id": "acme/my-model", "input": 1.5, "output": 3.0, "note": "contract"}
    ]
    assert client.get("/v1/ugen/pricing").json()["custom"][0]["id"] == "acme/my-model"

    found = client.get(
        "/v1/ugen/pricing/match", params={"name": "my-model-20260101"}
    ).json()
    assert (found["source"], found["matched"], found["input"]) == (
        "custom",
        "acme/my-model",
        1.5,
    )
    missing = client.get("/v1/ugen/pricing/match", params={"name": "nope-9"}).json()
    assert missing["source"] == "unknown"

    clash = client.put(
        "/v1/ugen/pricing",
        json={
            "custom": [
                {"id": "a-b", "input": 1, "output": 1},
                {"id": "A.B", "input": 2, "output": 2},
            ]
        },
    )
    assert clash.status_code == 422
    assert (
        client.put(
            "/v1/ugen/pricing", json={"custom": [{"id": "x", "input": -1, "output": 1}]}
        ).status_code
        == 422
    )
    del usage
