import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from ugen_ai_gateway.analysis.policy import RoutingPolicy
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

    async def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.completion


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
) -> tuple[GatewayService, FakeProvider, FakeProvider, FakeAnalyzer]:
    local = local_provider or FakeProvider()
    cloud = cloud_provider or FakeProvider()
    request_analyzer = analyzer or FakeAnalyzer()
    service = GatewayService(
        settings=Settings(
            local_model=local_model,
            cloud_model=cloud_model,
            cloud_api_key=cloud_api_key,
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
    assert cloud.calls[0]["messages"] == [{"role": "user", "content": "Email [EMAIL]"}]
    assert local.calls == []
    assert request.messages[0].get("content") == "Email alex@example.com"


def test_chat_redacts_personal_data_before_sending_to_local_provider() -> None:
    service, local, cloud, _ = make_service()
    request = ChatRequest(messages=[{"role": "user", "content": "Call 0912-345-678"}])

    asyncio.run(service.chat(request))

    assert local.calls[0]["messages"] == [
        {"role": "user", "content": "Call [TAIWAN_MOBILE]"}
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

    assert local.calls[0]["messages"][1]["content"][0]["text"] == "Email [EMAIL]"


def test_chat_rejects_unavailable_cloud_provider() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer, cloud_api_key="")
    request = ChatRequest(messages=[{"role": "user", "content": "solve this"}])

    with pytest.raises(RuntimeError, match="Cloud provider is unavailable"):
        asyncio.run(service.chat(request))

    assert local.calls == []
    assert cloud.calls == []


def test_chat_rejects_missing_cloud_model() -> None:
    analyzer = FakeAnalyzer(analysis(complexity="complex"))
    service, local, cloud, _ = make_service(analyzer=analyzer, cloud_model="")
    request = ChatRequest(messages=[{"role": "user", "content": "solve this"}])

    with pytest.raises(ValueError, match="No model configured for the cloud provider"):
        asyncio.run(service.chat(request))

    assert local.calls == []
    assert cloud.calls == []


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
