from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ugen_ai_gateway.api.schemas import ChatRequest, ChatResponse
from ugen_ai_gateway.dependencies import get_gateway_service
from ugen_ai_gateway.services.gateway import GatewayService

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
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Model provider request failed",
        ) from exc
