import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

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


def make_service(
    *,
    local_model: str = "local-default",
    cloud_model: str = "cloud-default",
    cloud_api_key: str = "cloud-key",
    local_provider: FakeProvider | None = None,
    cloud_provider: FakeProvider | None = None,
) -> tuple[GatewayService, FakeProvider, FakeProvider]:
    local = local_provider or FakeProvider()
    cloud = cloud_provider or FakeProvider()
    service = GatewayService(
        settings=Settings(
            local_model=local_model,
            cloud_model=cloud_model,
            cloud_api_key=cloud_api_key,
        ),
        local_provider=local,  # type: ignore[arg-type]
        cloud_provider=cloud,  # type: ignore[arg-type]
    )
    return service, local, cloud


def test_chat_uses_local_provider_and_default_model() -> None:
    service, local, cloud = make_service()
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    response = asyncio.run(service.chat(request))

    assert response.model == "resolved-model"
    assert response.content == "hello"
    assert response.provider == "local"
    assert local.calls[0]["model"] == "local-default"
    assert cloud.calls == []


def test_chat_uses_cloud_provider_and_request_model_override() -> None:
    service, local, cloud = make_service()
    request = ChatRequest(
        provider="cloud",
        model="requested-model",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
    )

    response = asyncio.run(service.chat(request))

    assert response.provider == "cloud"
    assert cloud.calls[0]["model"] == "requested-model"
    assert cloud.calls[0]["temperature"] == 0.7
    assert local.calls == []


def test_chat_rejects_cloud_when_api_key_is_missing() -> None:
    service, _, cloud = make_service(cloud_api_key="")
    request = ChatRequest(
        provider="cloud", messages=[{"role": "user", "content": "hi"}]
    )

    with pytest.raises(ValueError, match="Cloud provider is not configured"):
        asyncio.run(service.chat(request))

    assert cloud.calls == []


def test_chat_rejects_missing_model_configuration() -> None:
    service, local, _ = make_service(local_model="")
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(ValueError, match="No model configured"):
        asyncio.run(service.chat(request))

    assert local.calls == []


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
    service, _, _ = make_service(local_provider=FakeProvider(completion=completion))
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(exception, match=message):
        asyncio.run(service.chat(request))


def test_chat_propagates_provider_failure() -> None:
    service, _, _ = make_service(
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
