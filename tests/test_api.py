import asyncio
from importlib import import_module

import pytest
from fastapi import HTTPException, Request

from ugen_ai_gateway.api.routes import create_chat, health
from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse, RoutingInfo
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.services.gateway import GatewayService


def make_http_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/ugen/chat",
            "headers": [],
            "client": ("handler-test", 1234),
            "server": ("test-server", 80),
            "scheme": "http",
            "query_string": b"",
            "http_version": "1.1",
        }
    )


class FakeGateway(GatewayService):
    def __init__(self, result: ChatResponse | Exception) -> None:
        self.result = result

    async def chat(
        self, request: ChatRequest, *_: object, **__: object
    ) -> ChatResponse:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_health_returns_ok() -> None:
    assert asyncio.run(health()) == {"status": "ok"}


def test_api_access_requires_key_and_applies_per_ip_rate_limit(monkeypatch) -> None:
    routes_module = import_module("ugen_ai_gateway.api.access")
    settings = Settings(
        gateway_api_key="secret",
        rate_limit_requests=1,
        local_model="configured",
    )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    routes_module._requests_by_client.clear()

    def request(headers: list[tuple[bytes, bytes]]) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/ugen/chat",
                "headers": headers,
                "client": ("test-client", 1234),
                "server": ("test-server", 80),
                "scheme": "http",
                "query_string": b"",
                "http_version": "1.1",
            }
        )

    with pytest.raises(HTTPException) as unauthorized:
        routes_module.check_api_access(request([]))
    assert unauthorized.value.status_code == 401

    routes_module.check_api_access(request([(b"x-api-key", b"secret")]))
    with pytest.raises(HTTPException) as rate_limited:
        routes_module.check_api_access(request([(b"x-api-key", b"secret")]))
    assert rate_limited.value.status_code == 429


def test_chat_handler_returns_gateway_response(monkeypatch) -> None:
    routes_module = import_module("ugen_ai_gateway.api.routes")
    monkeypatch.setattr(routes_module, "check_api_access", lambda _: None)
    expected = ChatResponse(
        model="m",
        content="hello",
        provider="local",
        routing=RoutingInfo(
            complexity="simple",
            task_type="general",
            contains_personal_data=False,
            personal_data_types=[],
            provider="local",
            reason="Default local routing.",
        ),
    )
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    response = asyncio.run(
        create_chat(request, FakeGateway(expected), make_http_request())
    )

    assert response == expected


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (ValueError("model not configured"), 503, "model not configured"),
        (RuntimeError("provider offline"), 502, "Model provider request failed"),
    ],
)
def test_chat_handler_maps_errors_to_http(
    error: Exception, status_code: int, detail: str, monkeypatch
) -> None:
    routes_module = import_module("ugen_ai_gateway.api.routes")
    monkeypatch.setattr(routes_module, "check_api_access", lambda _: None)
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(create_chat(request, FakeGateway(error), make_http_request()))

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
