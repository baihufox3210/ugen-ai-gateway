"""OpenAI-compatible endpoints, so existing OpenAI SDKs and tools can use the
gateway by changing only their base URL."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ugen_ai_gateway.api.access import check_api_access
from ugen_ai_gateway.api.errors import http_error_for
from ugen_ai_gateway.api.schemas import MAX_CHAT_MESSAGES, ChatRequest, ChatResponse
from ugen_ai_gateway.config import Settings, get_settings
from ugen_ai_gateway.dependencies import get_gateway_service
from ugen_ai_gateway.services.gateway import GatewayEvent, GatewayService

logger = logging.getLogger(__name__)

router = APIRouter()

GatewayDependency = Annotated[GatewayService, Depends(get_gateway_service)]

AUTO_MODEL = "ugen-auto"
LOCAL_MODEL = "ugen-local"
CLOUD_MODEL = "ugen-cloud"
_ALIASES: dict[str, Literal["auto", "local", "cloud"]] = {
    AUTO_MODEL: "auto",
    "auto": "auto",
    LOCAL_MODEL: "local",
    CLOUD_MODEL: "cloud",
}
# Generation parameters forwarded to the provider when the client sets them.
_PASSTHROUGH_OPTIONS = (
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "seed",
)


class OpenAIChatRequest(BaseModel):
    """The subset of the OpenAI Chat Completions request the gateway supports."""

    model_config = ConfigDict(extra="ignore")

    model: str = AUTO_MODEL
    messages: list[ChatCompletionMessageParam] = Field(
        min_length=1, max_length=MAX_CHAT_MESSAGES
    )
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    seed: int | None = None
    n: int | None = None
    tools: list[Any] | None = None


class _RequestRejected(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def error_response(
    status_code: int, message: str, *, code: str | None = None
) -> JSONResponse:
    error_type = (
        "invalid_request_error"
        if status_code < 500
        else "server_error"
        if status_code != 503
        else "service_unavailable"
    )
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "code": code}},
    )


def resolve_model(name: str, settings: Settings) -> Literal["auto", "local", "cloud"]:
    """Map the requested model id to a provider selection."""
    if name in _ALIASES:
        return _ALIASES[name]
    if name and name == settings.local_model:
        return "local"
    if name and name == settings.cloud_model:
        return "cloud"
    raise _RequestRejected(404, f"The model '{name}' does not exist", "model_not_found")


def to_gateway_request(
    body: OpenAIChatRequest, settings: Settings
) -> tuple[ChatRequest, dict[str, Any]]:
    if body.n not in (None, 1):
        raise _RequestRejected(400, "Only n=1 is supported", "unsupported_parameter")
    if body.tools:
        raise _RequestRejected(
            400, "Tool calling is not supported yet", "unsupported_parameter"
        )
    payload: dict[str, Any] = {
        "provider_selection": resolve_model(body.model, settings),
        "messages": body.messages,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    try:
        request = ChatRequest.model_validate(payload)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "Invalid request")
        raise _RequestRejected(400, message) from exc
    options = {
        name: value
        for name in _PASSTHROUGH_OPTIONS
        if (value := getattr(body, name)) is not None
    }
    return request, options


def _usage(response: ChatResponse) -> dict[str, int] | None:
    usage = response.usage.generation if response.usage else None
    return usage.model_dump() if usage else None


def _ugen_details(response: ChatResponse) -> dict[str, Any]:
    return {
        "routing": response.routing.model_dump(mode="json"),
        "performance": (
            response.performance.model_dump(mode="json")
            if response.performance
            else None
        ),
    }


def _routing_headers(routing: dict[str, Any], model: str) -> dict[str, str]:
    return {
        "X-UGen-Provider": str(routing.get("provider", "")),
        "X-UGen-Model": model,
        "X-UGen-Route-Reason": str(routing.get("reason", "")),
        "X-UGen-Redacted-Values": str(routing.get("redacted_values", 0)),
    }


@router.get("/v1/models")
async def list_models() -> dict[str, Any]:
    settings = get_settings()
    cloud_enabled = bool(settings.cloud_api_key and settings.cloud_model)
    ids = [AUTO_MODEL, LOCAL_MODEL]
    if cloud_enabled:
        ids.append(CLOUD_MODEL)
    ids.extend(
        name
        for name in (settings.local_model, cloud_enabled and settings.cloud_model)
        if name and name not in ids
    )
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": 0, "owned_by": "ugen"}
            for model_id in ids
        ],
    }


@router.post("/v1/chat/completions", response_model=None)
async def create_chat_completion(
    body: OpenAIChatRequest,
    gateway: GatewayDependency,
    http_request: Request,
) -> JSONResponse | StreamingResponse:
    check_api_access(http_request)
    try:
        request, options = to_gateway_request(body, get_settings())
    except _RequestRejected as exc:
        return error_response(exc.status_code, str(exc), code=exc.code)

    completion_id = f"chatcmpl-ugen-{uuid.uuid4().hex}"
    created = int(time.time())

    if not body.stream:
        try:
            response = await gateway.chat(request, options=options)
        except Exception as exc:  # noqa: BLE001 - every failure maps to an API error
            return error_response(*http_error_for(exc))
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": response.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response.content},
                        "finish_reason": response.finish_reason or "stop",
                    }
                ],
                "usage": _usage(response),
                "ugen": _ugen_details(response),
            },
            headers=_routing_headers(
                response.routing.model_dump(mode="json"), response.model
            ),
        )

    # Run analysis and routing before answering, so request errors still get a
    # proper HTTP status and the routing decision can go into the headers.
    events = gateway.events(request, stream=True, options=options)
    routing: dict[str, Any] = {}
    model = ""
    try:
        async for event in events:
            if event.type == "routed":
                routing = event.data
            if event.type == "generation_started":
                model = str(event.data.get("model", ""))
                break
    except Exception as exc:  # noqa: BLE001 - every failure maps to an API error
        await events.aclose()
        return error_response(*http_error_for(exc))

    include_usage = bool((body.stream_options or {}).get("include_usage"))
    return StreamingResponse(
        _sse(events, completion_id, created, model, include_usage),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            **_routing_headers(routing, model),
        },
    )


async def _sse(
    events: AsyncIterator[GatewayEvent],
    completion_id: str,
    created: int,
    model: str,
    include_usage: bool,
) -> AsyncIterator[str]:
    def chunk(
        delta: dict[str, Any],
        finish_reason: str | None = None,
        **extra: Any,
    ) -> str:
        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            **extra,
        }
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    yield chunk({"role": "assistant", "content": ""})
    try:
        async for event in events:
            if event.type == "delta":
                yield chunk({"content": event.data["content"]})
            elif event.response is not None:
                response = event.response
                yield chunk(
                    {},
                    response.finish_reason or "stop",
                    ugen=_ugen_details(response),
                )
                if include_usage:
                    usage = _usage(response)
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [],
                                "usage": usage,
                            }
                        )
                        + "\n\n"
                    )
    except Exception as exc:  # noqa: BLE001 - every failure maps to an API error
        status_code, message = http_error_for(exc)
        error = {"message": message, "type": "server_error", "code": status_code}
        yield f"data: {json.dumps({'error': error})}\n\n"
    finally:
        await events.aclose()  # type: ignore[attr-defined]
    yield "data: [DONE]\n\n"
