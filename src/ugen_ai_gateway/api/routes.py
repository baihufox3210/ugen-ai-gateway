import hmac
import json
import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import get_gateway_service
from ugen_ai_gateway.services.errors import (
    GatewayConfigurationError,
    InvalidChatRequest,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    RequestTooLarge,
)
from ugen_ai_gateway.services.gateway import GatewayService

logger = logging.getLogger(__name__)

router = APIRouter()
_requests_by_client: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = Lock()

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


@router.get("/v1/models")
async def configured_models() -> dict[str, str]:
    """Expose configured model names for the local dashboard."""
    settings = get_settings()
    return {
        "local_model": settings.local_model.strip(),
        "cloud_model": settings.cloud_model.strip(),
    }


def check_api_access(request: Request) -> None:
    settings = get_settings()
    if settings.gateway_api_key and not hmac.compare_digest(
        request.headers.get("x-api-key", ""), settings.gateway_api_key
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _rate_lock:
        timestamps = _requests_by_client[client]
        cutoff = now - settings.rate_limit_window_seconds
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if len(timestamps) >= settings.rate_limit_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        timestamps.append(now)


@router.post("/v1/chat/completions", response_model=ChatResponse)
async def create_chat_completion(
    request: ChatRequest,
    gateway: GatewayDependency,
    http_request: Request,
) -> ChatResponse:
    check_api_access(http_request)
    try:
        return await gateway.chat(request)
    except RequestTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidChatRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GatewayConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderTimeout as exc:
        raise HTTPException(status_code=504, detail="Model provider timed out") from exc
    except ProviderRequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Model provider request failed",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Model provider timed out") from exc
    except Exception as exc:
        logger.exception("Unhandled chat completion error")
        raise HTTPException(
            status_code=502,
            detail="Model provider request failed",
        ) from exc


@router.post("/v1/chat/completions/stream")
async def create_chat_completion_stream(
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
