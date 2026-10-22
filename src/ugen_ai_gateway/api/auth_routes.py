"""Dashboard accounts: sign up, sign in, sign out and account deletion.

Sessions are bearer tokens (``ugs_…``) sent like API keys. The first account
becomes the administrator; others are members who see only their own usage.
"""

import base64
import binascii
import logging
import re
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from ugen_ai_gateway.api.access import (
    SESSION_PREFIX,
    audit_admin,
    presented_api_key,
    rate_limiter,
)
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import get_secret_box, get_usage_store
from ugen_ai_gateway.services.oidc import (
    CALLBACK_PATH,
    OIDC_SETTING,
    OidcClient,
    OidcError,
    OidcSettings,
    username_from,
)
from ugen_ai_gateway.services.usage import (
    AccountDisabled,
    UsageStore,
    UsernameTaken,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/ugen/auth")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]

GATEWAY_SETTING = "gateway"


def registration_allowed(store: UsageStore) -> bool:
    """Whether new accounts may sign up: the dashboard's setting once an
    administrator saved one, ALLOW_REGISTRATION before that."""
    saved = store.load_setting(GATEWAY_SETTING) or {}
    return bool(saved.get("allow_registration", get_settings().allow_registration))


@lru_cache
def get_oidc_client() -> OidcClient:
    return OidcClient()


def oidc_settings(store: UsageStore) -> OidcSettings:
    saved = store.load_setting(OIDC_SETTING)
    return OidcSettings.unsealed(saved, get_secret_box()) if saved else OidcSettings()


def callback_url(request: Request, settings: OidcSettings) -> str:
    """The URL the provider sends people back to; register it there."""
    if settings.redirect_uri:
        return settings.redirect_uri
    prefix = request.scope.get("ugen.api_prefix", "")
    return str(request.base_url).rstrip("/") + prefix + CALLBACK_PATH


def _dashboard_url(redirect_uri: str) -> str:
    base = redirect_uri.removesuffix(CALLBACK_PATH).removesuffix("/api")
    return base + "/"


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


# The dashboard resizes avatars to a small square before uploading.
_AVATAR_MAX_BYTES = 200_000
_AVATAR_TYPES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}


class AvatarUpdate(BaseModel):
    # A data URL (data:image/png;base64,...); null removes the avatar.
    image: str | None = Field(default=None, max_length=300_000)

    @field_validator("image")
    @classmethod
    def check_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        header, _, payload = value.partition(",")
        mime = header.removeprefix("data:").removesuffix(";base64")
        if not header.startswith("data:") or not header.endswith(";base64"):
            raise ValueError("Send the image as a base64 data URL")
        if mime not in _AVATAR_TYPES:
            raise ValueError("Use a PNG, JPEG or WebP image")
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("The image data is not valid base64") from exc
        if len(data) > _AVATAR_MAX_BYTES:
            raise ValueError("The image is too large")
        # Check the bytes really are that image type, never e.g. SVG or HTML.
        if not data.startswith(_AVATAR_TYPES[mime]) or (
            mime == "image/webp" and data[8:12] != b"WEBP"
        ):
            raise ValueError("The file is not a valid image")
        return value


class DeletionConfirmation(BaseModel):
    # The password, or for an account without one (single sign-on) the username.
    password: str = Field(default="", max_length=128)
    username: str = Field(default="", max_length=32)


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
            "sso": None,
        }
    has_users = await store.has_users()
    sso = oidc_settings(store)
    return {
        "accounts_enabled": True,
        "has_users": has_users,
        # The first account can always be created, so the gateway gets an admin.
        "registration_open": registration_allowed(store) or not has_users,
        "sso": (
            {"label": sso.button_label, "provider": sso.provider} if sso.ready else None
        ),
    }


@router.get("/oidc/start")
async def oidc_start(store: UsageStoreDependency, request: Request) -> Response:
    """Send the browser to the identity provider to sign in."""
    store = _require_store(store)
    _limit_attempts(request)
    settings = oidc_settings(store)
    redirect_uri = callback_url(request, settings)
    if not settings.ready:
        return _sso_failed(redirect_uri, "Single sign-on is not set up")
    try:
        target = await get_oidc_client().authorization_url(settings, redirect_uri)
    except OidcError as exc:
        return _sso_failed(redirect_uri, str(exc))
    return RedirectResponse(target, status_code=302)


@router.get("/oidc/callback")
async def oidc_callback(
    store: UsageStoreDependency,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
) -> Response:
    """Finish signing in and hand the dashboard a session in the URL fragment,
    which never reaches a server or a log."""
    store = _require_store(store)
    settings = oidc_settings(store)
    client = get_oidc_client()
    redirect_uri = client.take_redirect_uri(state) or callback_url(request, settings)
    if error:
        return _sso_failed(redirect_uri, error_description or error)
    if not settings.ready:
        return _sso_failed(redirect_uri, "Single sign-on is not set up")
    try:
        identity = await client.finish(settings, code, state)
        user = await store.sso_user(
            identity.subject,
            identity.email,
            username_from(identity.name_hint),
            create=settings.auto_create,
        )
    except OidcError as exc:
        return _sso_failed(redirect_uri, str(exc))
    except AccountDisabled:
        return _sso_failed(
            redirect_uri, "This account is disabled; ask an administrator"
        )
    if user is None:
        return _sso_failed(
            redirect_uri,
            "No account here is linked to your sign-in; ask an administrator",
        )
    if user["created_at"] == user["last_login_at"]:
        await store.append_audit(
            "account_created",
            request_id=None,
            detail={
                "actor": user["id"],
                "user_id": user["id"],
                "is_admin": user["is_admin"],
                "sso": True,
            },
        )
    session = await _signed_in(store, user)
    return RedirectResponse(
        f"{_dashboard_url(redirect_uri)}#sso={session['token']}", status_code=302
    )


def _sso_failed(redirect_uri: str, message: str) -> Response:
    logger.info("Single sign-on failed: %s", message)
    return RedirectResponse(
        f"{_dashboard_url(redirect_uri)}#sso_error={quote(message)}", status_code=302
    )


@router.post("/register", status_code=201)
async def register(
    body: Credentials, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = _require_store(store)
    _limit_attempts(request)
    if not registration_allowed(store) and await store.has_users():
        raise HTTPException(status_code=403, detail="Sign-up is closed")
    try:
        user = await store.create_user(body.username, body.password)
    except UsernameTaken as exc:
        raise HTTPException(status_code=409, detail="That username is taken") from exc
    await store.append_audit(
        "account_created",
        request_id=None,
        detail={
            "actor": user["id"],
            "user_id": user["id"],
            "is_admin": user["is_admin"],
        },
    )
    return await _signed_in(store, user)


@router.post("/login")
async def login(
    body: Credentials, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = _require_store(store)
    _limit_attempts(request)
    try:
        user = await store.verify_login(body.username, body.password)
    except AccountDisabled as exc:
        raise HTTPException(
            status_code=403, detail="This account is disabled; ask an administrator"
        ) from exc
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


@router.put("/me/avatar")
async def update_avatar(
    body: AvatarUpdate, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    store = _require_store(store)
    _, user = await _session_user(request, store)
    updated = await store.set_avatar(user["id"], body.image)
    if updated is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return updated


class PasswordChange(BaseModel):
    # Empty for an account without a password yet (single sign-on).
    current_password: str = Field(default="", max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.put("/me/password", status_code=204)
async def change_password(
    body: PasswordChange, store: UsageStoreDependency, request: Request
) -> Response:
    """Change the password; other browsers signed in to the account are signed out."""
    store = _require_store(store)
    _limit_attempts(request)
    token, user = await _session_user(request, store)
    if user["has_password"] and (
        await store.verify_login(user["username"], body.current_password) is None
    ):
        raise HTTPException(status_code=403, detail="The current password is wrong")
    await store.change_password(user["id"], body.new_password, keep_session=token)
    await audit_admin(request, store, "password_changed", user_id=user["id"])
    return Response(status_code=204)


@router.delete("/me", status_code=204)
async def delete_account(
    body: DeletionConfirmation, store: UsageStoreDependency, request: Request
) -> Response:
    """Delete the signed-in account; the password must be entered again (the
    username, for an account that signs in only through single sign-on)."""
    store = _require_store(store)
    _limit_attempts(request)
    _, user = await _session_user(request, store)
    if not user["has_password"]:
        if body.username.lower() != user["username"].lower():
            raise HTTPException(status_code=403, detail="Type your username to confirm")
    elif await store.verify_login(user["username"], body.password) is None:
        raise HTTPException(status_code=403, detail="Wrong password")
    await audit_admin(request, store, "account_deleted", user_id=user["id"])
    await store.delete_user(user["id"])
    return Response(status_code=204)
