import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
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

GatewayDependency = Annotated[
    GatewayService,
    Depends(get_gateway_service),
]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/chat/completions", response_model=ChatResponse)
async def create_chat_completion(
    request: ChatRequest,
    gateway: GatewayDependency,
) -> ChatResponse:
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
