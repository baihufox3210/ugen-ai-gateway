"""Account administration and gateway-wide settings, for administrators."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ugen_ai_gateway.api.access import audit_admin, check_admin_access, identify
from ugen_ai_gateway.api.auth_routes import GATEWAY_SETTING, registration_allowed
from ugen_ai_gateway.dependencies import get_usage_store
from ugen_ai_gateway.services.usage import LastAdministrator, UsageStore

router = APIRouter(prefix="/v1/ugen")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]


def _require_store(store: UsageStore | None) -> UsageStore:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Accounts need a database; set USAGE_DB_PATH"
        )
    return store


async def _admin(request: Request, store: UsageStore | None) -> UsageStore:
    await check_admin_access(request, store)
    return _require_store(store)


async def _not_self(request: Request, store: UsageStore, user_id: str) -> None:
    principal = await identify(request, store)
    if principal is not None and principal.user_id == user_id:
        raise HTTPException(
            status_code=400, detail="Use the Account page to change your own account"
        )


@router.get("/users")
async def list_users(store: UsageStoreDependency, request: Request) -> dict[str, Any]:
    """Every account with this month's usage, highest cloud cost first."""
    store = await _admin(request, store)
    return {"data": await store.list_users()}


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    disabled: bool | None = None
    # Monthly cloud budget in USD; null removes the limit.
    monthly_budget_usd: float | None = Field(default=None, ge=0, le=1_000_000)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str, body: UserUpdate, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = await _admin(request, store)
    changes = body.model_dump(exclude_unset=True)
    if "is_admin" in changes or "disabled" in changes:
        await _not_self(request, store, user_id)
    try:
        user = await store.update_user(user_id, changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown account") from exc
    except LastAdministrator as exc:
        raise HTTPException(
            status_code=409,
            detail="The gateway needs at least one active administrator",
        ) from exc
    await audit_admin(request, store, "account_updated", user_id=user_id, **changes)
    return user


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str, store: UsageStoreDependency, request: Request
) -> dict[str, str]:
    """Set a random password and sign the account out everywhere. The new
    password is shown once, for the administrator to pass on."""
    store = await _admin(request, store)
    await _not_self(request, store, user_id)
    if await store.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="Unknown account")
    password = secrets.token_urlsafe(12)
    await store.change_password(user_id, password)
    await audit_admin(request, store, "password_reset", user_id=user_id)
    return {"password": password}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str, store: UsageStoreDependency, request: Request
) -> Response:
    store = await _admin(request, store)
    await _not_self(request, store, user_id)
    if not await store.delete_user(user_id):
        raise HTTPException(status_code=404, detail="Unknown account")
    await audit_admin(request, store, "account_deleted", user_id=user_id)
    return Response(status_code=204)


class GatewaySettings(BaseModel):
    allow_registration: bool


@router.get("/settings")
async def get_gateway_settings(
    store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = await _admin(request, store)
    return {"allow_registration": registration_allowed(store)}


@router.put("/settings")
async def save_gateway_settings(
    body: GatewaySettings, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = await _admin(request, store)
    saved = store.load_setting(GATEWAY_SETTING) or {}
    await store.save_setting(GATEWAY_SETTING, {**saved, **body.model_dump()})
    await audit_admin(request, store, "settings_updated", **body.model_dump())
    return body.model_dump()
