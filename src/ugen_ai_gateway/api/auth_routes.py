"""Dashboard accounts: sign up, sign in, sign out and account deletion.

Sessions are bearer tokens (``ugs_…``) sent like API keys. The first account
becomes the administrator; others are members who see only their own usage.
"""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from ugen_ai_gateway.api.access import (
    SESSION_PREFIX,
    presented_api_key,
    rate_limiter,
)
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import get_usage_store
from ugen_ai_gateway.services.usage import UsageStore, UsernameTaken

router = APIRouter(prefix="/v1/ugen/auth")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]

_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# Sign-in and sign-up attempts per client IP per minute.
_AUTH_ATTEMPTS_PER_MINUTE = 10


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def check_username(cls, value: str) -> str:
        if not _USERNAME.match(value):
            raise ValueError("Use 3-32 letters, digits, dots, dashes or underscores")
        return value


class PasswordConfirmation(BaseModel):
    password: str = Field(min_length=1, max_length=128)


def _require_store(store: UsageStore | None) -> UsageStore:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Accounts need a database; set USAGE_DB_PATH"
        )
    return store


def _limit_attempts(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not rate_limiter.hit(
        f"auth:{client}", limit=_AUTH_ATTEMPTS_PER_MINUTE, window_seconds=60
    ):
        raise HTTPException(
            status_code=429, detail="Too many attempts; try again in a minute"
        )


async def _session_user(
    request: Request, store: UsageStore
) -> tuple[str, dict[str, Any]]:
    token = presented_api_key(request)
    user = await store.find_session(token) if token.startswith(SESSION_PREFIX) else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return token, user


async def _signed_in(store: UsageStore, user: dict[str, Any]) -> dict[str, Any]:
    ttl = get_settings().session_ttl_days * 86400
    token, expires_at = await store.create_session(user["id"], ttl_seconds=ttl)
    return {"token": token, "expires_at": expires_at, "user": user}


@router.get("/status")
async def auth_status(store: UsageStoreDependency) -> dict[str, Any]:
    """What the sign-in screen should offer. Needs no credentials."""
    if store is None:
        return {
            "accounts_enabled": False,
            "has_users": False,
            "registration_open": False,
        }
    has_users = await store.has_users()
    return {
        "accounts_enabled": True,
        "has_users": has_users,
        # The first account can always be created, so the gateway gets an admin.
        "registration_open": get_settings().allow_registration or not has_users,
    }


@router.post("/register", status_code=201)
async def register(
    body: Credentials, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = _require_store(store)
    _limit_attempts(request)
    if not get_settings().allow_registration and await store.has_users():
        raise HTTPException(status_code=403, detail="Sign-up is closed")
    try:
        user = await store.create_user(body.username, body.password)
    except UsernameTaken as exc:
        raise HTTPException(status_code=409, detail="That username is taken") from exc
    return await _signed_in(store, user)


@router.post("/login")
async def login(
    body: Credentials, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = _require_store(store)
    _limit_attempts(request)
    user = await store.verify_login(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    return await _signed_in(store, user)


@router.post("/logout", status_code=204)
async def logout(store: UsageStoreDependency, request: Request) -> Response:
    token = presented_api_key(request)
    if store is not None and token.startswith(SESSION_PREFIX):
        await store.delete_session(token)
    return Response(status_code=204)


@router.get("/me")
async def current_user(store: UsageStoreDependency, request: Request) -> dict[str, Any]:
    _, user = await _session_user(request, _require_store(store))
    return user


@router.delete("/me", status_code=204)
async def delete_account(
    body: PasswordConfirmation, store: UsageStoreDependency, request: Request
) -> Response:
    """Delete the signed-in account; the password must be entered again."""
    store = _require_store(store)
    _limit_attempts(request)
    _, user = await _session_user(request, store)
    if await store.verify_login(user["username"], body.password) is None:
        raise HTTPException(status_code=403, detail="Wrong password")
    await store.delete_user(user["id"])
    return Response(status_code=204)
