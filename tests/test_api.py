import asyncio

import pytest
from fastapi import HTTPException

from ugen_ai_gateway.api.routes import create_chat_completion, health
from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
from ugen_ai_gateway.services.gateway import GatewayService


class FakeGateway(GatewayService):
    def __init__(self, result: ChatResponse | Exception) -> None:
        self.result = result

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_health_returns_ok() -> None:
    assert asyncio.run(health()) == {"status": "ok"}


def test_chat_handler_returns_gateway_response() -> None:
    expected = ChatResponse(model="m", content="hello", provider="local")
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    response = asyncio.run(create_chat_completion(request, FakeGateway(expected)))

    assert response == expected


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (ValueError("model not configured"), 503, "model not configured"),
        (RuntimeError("provider offline"), 502, "Model provider request failed"),
    ],
)
def test_chat_handler_maps_errors_to_http(
    error: Exception, status_code: int, detail: str
) -> None:
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(create_chat_completion(request, FakeGateway(error)))

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
