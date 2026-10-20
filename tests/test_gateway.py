import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from ugen_ai_gateway.analysis.policy import RoutingPolicy
from ugen_ai_gateway.analysis.redaction import PLACEHOLDER_INSTRUCTION
from ugen_ai_gateway.analysis.schemas import RequestAnalysis
from ugen_ai_gateway.api.schemas import ChatRequest
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.services.gateway import GatewayService


class FakeProvider:
    def __init__(self, completion: Any = None, error: Exception | None = None) -> None:
        self.completion = completion or SimpleNamespace(
            model="resolved-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.chunks: list[Any] = []

    async def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.completion

    async def stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk


def text_chunk(text: str) -> Any:
    return SimpleNamespace(
        model="streamed-model",
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
    )


def usage_chunk(completion_tokens: int) -> Any:
    return SimpleNamespace(
        model="streamed-model",
        usage=SimpleNamespace(
            prompt_tokens=3,
            completion_tokens=completion_tokens,
            total_tokens=3 + completion_tokens,
        ),
        choices=[],
    )


class FakeAnalyzer:
    def __init__(self, analysis: RequestAnalysis | None = None) -> None:
        self.analysis = analysis or RequestAnalysis(
            contains_personal_data=False,
            personal_data_types=[],
            complexity="simple",
            task_type="general",
            reason="Simple general request.",
        )
        self.inputs: list[str] = []

    async def analyze(self, user_input: str) -> RequestAnalysis:
        self.inputs.append(user_input)
        return self.analysis


def make_service(
    *,
    local_model: str = "local-default",
    cloud_model: str = "cloud-default",
    cloud_api_key: str = "cloud-key",
    local_provider: FakeProvider | None = None,
    cloud_provider: FakeProvider | None = None,
    analyzer: FakeAnalyzer | None = None,
    cloud_enabled: bool = True,
    **settings: Any,
) -> tuple[GatewayService, FakeProvider, FakeProvider, FakeAnalyzer]:
    local = local_provider or FakeProvider()
    cloud = cloud_provider or FakeProvider()
    request_analyzer = analyzer or FakeAnalyzer()
    service = GatewayService(
        settings=Settings(
            local_model=local_model,
            cloud_model=cloud_model,
            cloud_api_key=cloud_api_key,
            **{"analysis_mode": "llm", "analysis_cache_size": 0, **settings},
        ),
        local_provider=local,  # type: ignore[arg-type]
        cloud_provider=cloud if cloud_api_key else None,  # type: ignore[arg-type]
        analyzer=request_analyzer,  # type: ignore[arg-type]
        policy=RoutingPolicy(cloud_enabled=cloud_enabled),
    )
    return service, local, cloud, request_analyzer


def analysis(
    *, contains_personal_data: bool = False, complexity: str = "simple"
) -> RequestAnalysis:
    return RequestAnalysis(
        contains_personal_data=contains_personal_data,
        personal_data_types=["email"] if contains_personal_data else [],
        complexity=complexity,  # type: ignore[arg-type]
        task_type="general",
        reason="Test analysis.",
    )


def test_chat_uses_local_provider_and_default_model() -> None:
    service, local, cloud, analyzer = make_service()
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    response = asyncio.run(service.chat(request))

    assert response.model == "resolved-model"
    assert response.content == "hello"
    assert response.provider == "local"
    assert response.routing.complexity == "simple"
    assert response.routing.task_type == "general"
    assert response.routing.contains_personal_data is False
    assert response.routing.provider == "local"
    assert (
        response.routing.reason == "Local model selected by the default routing policy."
    )
    assert analyzer.inputs == ["hi"]
    assert local.calls[0]["model"] == "local-default"
    assert cloud.calls == []


def test_chat_uses_cloud_for_complex_request() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer)
    request = ChatRequest(messages=[{"role": "user", "content": "solve this"}])

    response = asyncio.run(service.chat(request))

    assert response.provider == "cloud"
    assert response.routing.complexity == "complex"
    assert response.routing.provider == "cloud"
    assert cloud.calls[0]["model"] == "cloud-default"
    assert local.calls == []


def test_chat_redacts_personal_data_and_routes_complex_request_to_cloud() -> None:
    analyzer = FakeAnalyzer(analysis(contains_personal_data=True, complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer)
    request = ChatRequest(
        messages=[{"role": "user", "content": "Email alex@example.com"}]
    )

    response = asyncio.run(service.chat(request))

    assert response.provider == "cloud"
    assert response.routing.redacted_values == 1
    assert cloud.calls[0]["messages"] == [
        {"role": "system", "content": PLACEHOLDER_INSTRUCTION},
        {"role": "user", "content": "Email [EMAIL_1]"},
    ]
    assert local.calls == []
    assert request.messages[0].get("content") == "Email alex@example.com"


def test_chat_redacts_personal_data_before_sending_to_local_provider() -> None:
    service, local, cloud, _ = make_service()
    request = ChatRequest(messages=[{"role": "user", "content": "Call 0912-345-678"}])

    asyncio.run(service.chat(request))

    assert local.calls[0]["messages"][1:] == [
        {"role": "user", "content": "Call [TAIWAN_MOBILE_1]"}
    ]
    assert cloud.calls == []


@pytest.mark.parametrize("contains_personal_data", [True, False])
def test_chat_rejects_unredactable_personal_data_without_provider_forwarding(
    contains_personal_data: bool,
) -> None:
    analyzer = FakeAnalyzer(
        RequestAnalysis(
            contains_personal_data=contains_personal_data,
            personal_data_types=["name"],
            complexity="complex",
            task_type="general",
            reason="A name was detected.",
        )
    )
    service, local, cloud, _ = make_service(analyzer=analyzer)
    request = ChatRequest(messages=[{"role": "user", "content": "My name is Alex"}])

    with pytest.raises(ValueError, match="could not be safely redacted"):
        asyncio.run(service.chat(request))

    assert local.calls == []
    assert cloud.calls == []


def test_chat_keeps_complex_request_local_when_cloud_is_disabled() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer, cloud_enabled=False)
    request = ChatRequest(messages=[{"role": "user", "content": "solve this"}])

    response = asyncio.run(service.chat(request))

    assert response.provider == "local"
    assert local.calls[0]["model"] == "local-default"
    assert cloud.calls == []


def test_chat_uses_request_model_override_for_selected_provider() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer)
    request = ChatRequest(
        model="requested-model",
        messages=[{"role": "user", "content": "solve this"}],
        temperature=0.7,
    )

    response = asyncio.run(service.chat(request))

    assert response.provider == "cloud"
    assert cloud.calls[0]["model"] == "requested-model"
    assert cloud.calls[0]["temperature"] == 0.7
    assert local.calls == []


def test_chat_ignores_requested_provider_and_uses_policy() -> None:
    service, local, cloud, _ = make_service()
    request = ChatRequest(
        provider="cloud", messages=[{"role": "user", "content": "hi"}]
    )

    response = asyncio.run(service.chat(request))

    assert response.provider == "local"
    assert local.calls
    assert cloud.calls == []


def test_chat_rejects_missing_local_model() -> None:
    service, local, cloud, analyzer = make_service(local_model="")
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(ValueError, match="LOCAL_MODEL is not configured"):
        asyncio.run(service.chat(request))

    assert analyzer.inputs == []
    assert local.calls == []
    assert cloud.calls == []


def test_chat_requires_text_user_message() -> None:
    service, local, cloud, analyzer = make_service()
    request = ChatRequest(
        messages=[{"role": "user", "content": [{"type": "text", "text": "image"}]}]
    )

    with pytest.raises(ValueError, match="A text user message is required"):
        asyncio.run(service.chat(request))

    assert analyzer.inputs == []
    assert local.calls == []
    assert cloud.calls == []


def test_chat_analyzes_only_string_user_messages() -> None:
    service, _, _, analyzer = make_service()
    request = ChatRequest(
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": [{"type": "text", "text": "ignored"}]},
            {"role": "user", "content": "second"},
        ]
    )

    asyncio.run(service.chat(request))

    assert analyzer.inputs == ["first\nsecond"]


def test_chat_redacts_text_parts_in_multimodal_messages() -> None:
    service, local, _, _ = make_service()
    request = ChatRequest(
        messages=[
            {"role": "user", "content": "Review this image"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Email alex@example.com"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/x.png"},
                    },
                ],
            },
        ]
    )

    asyncio.run(service.chat(request))

    assert local.calls[0]["messages"][2]["content"][0]["text"] == "Email [EMAIL_1]"


def test_chat_rejects_explicit_cloud_selection_without_cloud_provider() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer, cloud_api_key="")
    request = ChatRequest(
        provider_selection="cloud",
        messages=[{"role": "user", "content": "solve this"}],
    )

    with pytest.raises(RuntimeError, match="Cloud provider is unavailable"):
        asyncio.run(service.chat(request))

    assert local.calls == []
    assert cloud.calls == []


def test_chat_keeps_complex_request_local_without_cloud_model() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, _, cloud, _ = make_service(analyzer=analyzer, cloud_model="")
    request = ChatRequest(messages=[{"role": "user", "content": "solve this"}])

    assert asyncio.run(service.chat(request)).provider == "local"
    assert cloud.calls == []

    explicit = ChatRequest(
        provider_selection="cloud", messages=[{"role": "user", "content": "x"}]
    )
    with pytest.raises(RuntimeError, match="Cloud provider is unavailable"):
        asyncio.run(service.chat(explicit))


@pytest.mark.parametrize(
    ("completion", "exception", "message"),
    [
        (SimpleNamespace(model="m", choices=[]), RuntimeError, "no choices"),
        (
            SimpleNamespace(
                model="m",
                choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
            ),
            TypeError,
            "no text content",
        ),
    ],
)
def test_chat_rejects_invalid_provider_response(
    completion: Any, exception: type[Exception], message: str
) -> None:
    service, _, _, _ = make_service(local_provider=FakeProvider(completion=completion))
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(exception, match=message):
        asyncio.run(service.chat(request))


def test_chat_propagates_provider_failure() -> None:
    service, _, _, _ = make_service(
        local_provider=FakeProvider(error=RuntimeError("offline"))
    )
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(service.chat(request))


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "other", "messages": [{"role": "user", "content": "hi"}]},
        {"messages": []},
        {
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 2.1,
        },
    ],
)
def test_chat_request_rejects_invalid_payload(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ChatRequest.model_validate(payload)


async def collect(service: GatewayService, request: ChatRequest) -> list[Any]:
    return [event async for event in service.events(request, stream=True)]


def test_events_stream_text_deltas_then_result() -> None:
    local = FakeProvider()
    local.chunks = [text_chunk("Hel"), text_chunk("lo"), usage_chunk(2)]
    service, _, cloud, _ = make_service(local_provider=local, output_pii_action="off")
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    events = asyncio.run(collect(service, request))

    assert [event.type for event in events] == [
        "analysis_started",
        "analysis_completed",
        "routed",
        "generation_started",
        "delta",
        "delta",
        "result",
    ]
    assert [event.data["content"] for event in events if event.type == "delta"] == [
        "Hel",
        "lo",
    ]
    result = events[-1].response
    assert result.content == "Hello"
    assert result.model == "streamed-model"
    assert result.usage.generation.completion_tokens == 2
    assert events[-1].to_json()["data"]["content"] == "Hello"
    assert cloud.calls == []


def test_events_stream_wraps_provider_failure() -> None:
    service, _, _, _ = make_service(
        local_provider=FakeProvider(error=RuntimeError("offline"))
    )
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(RuntimeError, match="Model provider request failed: offline"):
        asyncio.run(collect(service, request))


def reply(content: str) -> Any:
    return SimpleNamespace(
        model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_chat_restores_placeholders_in_the_reply() -> None:
    local = FakeProvider(completion=reply("I will email [EMAIL_1] today."))
    service, _, _, _ = make_service(local_provider=local)
    request = ChatRequest(
        messages=[{"role": "user", "content": "Write to alex@example.com"}]
    )

    response = asyncio.run(service.chat(request))

    assert response.content == "I will email alex@example.com today."
    assert "alex@example.com" not in str(local.calls[0]["messages"])


def test_chat_keeps_placeholders_when_restore_is_disabled() -> None:
    local = FakeProvider(completion=reply("Mail [EMAIL_1]"))
    service, _, _, _ = make_service(local_provider=local, restore_redacted=False)
    request = ChatRequest(messages=[{"role": "user", "content": "alex@example.com"}])

    assert asyncio.run(service.chat(request)).content == "Mail [EMAIL_1]"


def test_chat_sends_raw_text_to_local_model_when_local_redaction_is_off() -> None:
    service, local, _, _ = make_service(redact_local=False)
    request = ChatRequest(messages=[{"role": "user", "content": "Call 0912-345-678"}])

    response = asyncio.run(service.chat(request))

    assert local.calls[0]["messages"] == [
        {"role": "user", "content": "Call 0912-345-678"}
    ]
    assert response.routing.redacted_values == 0


def test_cloud_requests_are_redacted_even_when_local_redaction_is_off() -> None:
    analyzer = FakeAnalyzer(analysis(contains_personal_data=True, complexity="complex"))
    service, _, cloud, _ = make_service(analyzer=analyzer, redact_local=False)
    request = ChatRequest(messages=[{"role": "user", "content": "alex@example.com"}])

    asyncio.run(service.chat(request))

    assert cloud.calls[0]["messages"][-1]["content"] == "[EMAIL_1]"


def test_same_value_gets_the_same_placeholder_across_turns() -> None:
    service, local, _, _ = make_service()
    request = ChatRequest(
        messages=[
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Email alex@example.com"},
            {"role": "assistant", "content": "Sent to alex@example.com."},
            {"role": "user", "content": "Also cc sam@example.org and alex@example.com"},
        ]
    )

    asyncio.run(service.chat(request))

    sent = local.calls[0]["messages"]
    assert sent[0]["content"] == f"Be brief.\n\n{PLACEHOLDER_INSTRUCTION}"
    assert sent[1]["content"] == "Email [EMAIL_1]"
    assert sent[2]["content"] == "Sent to [EMAIL_1]."
    assert sent[3]["content"] == "Also cc [EMAIL_2] and [EMAIL_1]"


def test_stream_restores_placeholders_split_across_chunks() -> None:
    local = FakeProvider()
    local.chunks = [text_chunk("Mail [EM"), text_chunk("AIL_"), text_chunk("1] now")]
    service, _, _, _ = make_service(local_provider=local, output_pii_action="off")
    request = ChatRequest(messages=[{"role": "user", "content": "alex@example.com"}])

    events = asyncio.run(collect(service, request))

    deltas = [event.data["content"] for event in events if event.type == "delta"]
    assert "".join(deltas) == "Mail alex@example.com now"
    assert all("[" not in delta for delta in deltas)
    assert events[-1].response.content == "Mail alex@example.com now"


def test_hybrid_analysis_skips_the_model_when_the_request_stays_local() -> None:
    service, local, _, analyzer = make_service(analysis_mode="hybrid")
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    response = asyncio.run(service.chat(request))

    assert analyzer.inputs == []
    assert response.routing.analysis_source == "rules"
    assert response.provider == "local"
    assert local.calls


@pytest.mark.parametrize(
    ("content", "selection"),
    [
        ("Please design a system for billing", "auto"),
        ("hi", "cloud"),
    ],
)
def test_hybrid_analysis_uses_the_model_whenever_cloud_is_possible(
    content: str, selection: str
) -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, _, cloud, _ = make_service(analyzer=analyzer, analysis_mode="hybrid")
    request = ChatRequest(
        provider_selection=selection, messages=[{"role": "user", "content": content}]
    )

    response = asyncio.run(service.chat(request))

    assert analyzer.inputs == [content]
    assert response.routing.analysis_source == "llm"
    assert cloud.calls


def test_hybrid_analysis_skips_the_model_when_local_is_forced() -> None:
    service, _, _, analyzer = make_service(analysis_mode="hybrid")
    request = ChatRequest(
        provider_selection="local",
        messages=[{"role": "user", "content": "Please design a system"}],
    )

    response = asyncio.run(service.chat(request))

    assert analyzer.inputs == []
    assert response.routing.complexity == "complex"


def test_rules_mode_never_calls_the_analysis_model() -> None:
    service, _, cloud, analyzer = make_service(analysis_mode="rules")
    request = ChatRequest(
        messages=[{"role": "user", "content": "Compare these designs, mail a@b.com"}]
    )

    response = asyncio.run(service.chat(request))

    assert analyzer.inputs == []
    assert response.routing.personal_data_types == ["email"]
    assert response.provider == "cloud"
    assert cloud.calls[0]["messages"][-1]["content"].endswith("[EMAIL_1]")


def test_analysis_results_are_cached_for_identical_input() -> None:
    service, _, _, analyzer = make_service(analysis_cache_size=8)
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    first = asyncio.run(service.chat(request))
    second = asyncio.run(service.chat(request))

    assert analyzer.inputs == ["hi"]
    assert first.routing.analysis_source == "llm"
    assert second.routing.analysis_source == "cache"


def test_analysis_events_report_the_source() -> None:
    service, _, _, _ = make_service(analysis_mode="hybrid")
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    events = asyncio.run(collect(service, request))

    completed = next(event for event in events if event.type == "analysis_completed")
    assert completed.data["source"] == "rules"


def test_personal_data_inside_chinese_text_is_redacted_and_restored() -> None:
    analyzer = FakeAnalyzer(
        RequestAnalysis(
            contains_personal_data=True,
            personal_data_types=["name", "taiwan_mobile"],
            personal_data_values=["王小明"],
            complexity="complex",
            task_type="general",
            reason="test",
        )
    )
    cloud = FakeProvider(
        completion=reply("已通知[PERSONAL_DATA_1]（[TAIWAN_MOBILE_1]）")
    )
    service, _, _, _ = make_service(analyzer=analyzer, cloud_provider=cloud)
    request = ChatRequest(
        messages=[{"role": "user", "content": "請通知王小明，電話0912345678"}]
    )

    response = asyncio.run(service.chat(request))

    assert cloud.calls[0]["messages"][-1]["content"] == (
        "請通知[PERSONAL_DATA_1]，電話[TAIWAN_MOBILE_1]"
    )
    assert response.content == "已通知王小明（0912345678）"
