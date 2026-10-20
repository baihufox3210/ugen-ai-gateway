"""Gateway-specific endpoints used by the dashboard: health, config, and chat
with routing details and stage-by-stage progress."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ugen_ai_gateway.api.access import check_api_access
from ugen_ai_gateway.api.errors import http_error_for
from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import get_gateway_service
from ugen_ai_gateway.services.gateway import GatewayService

logger = logging.getLogger(__name__)

router = APIRouter()

GatewayDependency = Annotated[
    GatewayService,
    Depends(get_gateway_service),
]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness(gateway: GatewayDependency, response: Response) -> dict[str, str]:
    if not await gateway.readiness():
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready"}


@router.get("/v1/ugen/config")
async def gateway_config(gateway: GatewayDependency) -> dict[str, Any]:
    """Expose non-secret configuration for the dashboard."""
    settings = get_settings()
    engine = gateway.engine
    local = engine.default("local")
    cloud = engine.default("cloud")
    return {
        "local_model": local.model.id if local else "",
        "cloud_model": cloud.model.id if cloud else "",
        "cloud_enabled": cloud is not None,
        "redact_local": settings.redact_local,
        "restore_redacted": settings.restore_redacted,
        "providers": [
            {
                "name": provider.name,
                "locality": provider.locality,
                "available": provider.name in engine.available,
                "models": [model.id for model in provider.models],
            }
            for provider in engine.config.providers
        ],
        "rules": [rule.name for rule in engine.config.rules],
        "never_cloud": engine.config.privacy.never_cloud,
    }


@router.get("/v1/ugen/providers")
async def provider_status(gateway: GatewayDependency) -> dict[str, str]:
    """Circuit-breaker state per provider: closed, open, or half_open."""
    return gateway.provider_status()


@router.post("/v1/ugen/chat", response_model=ChatResponse)
async def create_chat(
    request: ChatRequest,
    gateway: GatewayDependency,
    http_request: Request,
) -> ChatResponse:
    check_api_access(http_request)
    try:
        return await gateway.chat(request)
    except Exception as exc:
        status_code, detail = http_error_for(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/v1/ugen/chat/stream")
async def create_chat_stream(
    request: ChatRequest,
    gateway: GatewayDependency,
    http_request: Request,
) -> StreamingResponse:
    check_api_access(http_request)

    async def events():
        try:
            async for event in gateway.events(request, stream=True):
                yield json.dumps(event.to_json(), ensure_ascii=False) + "\n"
        except Exception as exc:
            logger.exception("Streaming chat completion failed")
            yield (
                json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False)
                + "\n"
            )

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
