import hmac
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Protocol

from fastapi import HTTPException, Request

from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.services.keys import Principal
from ugen_ai_gateway.services.usage import UsageStore


class RateLimiter(Protocol):
    def hit(self, bucket: str, *, limit: int, window_seconds: float) -> bool:
        """Count one request; False when the bucket is over its limit."""
        ...


class InMemoryRateLimiter:
    """Sliding-window limiter for a single process. Use a shared store (such as
    Redis) behind the same interface when running several workers."""

    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, bucket: str, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            timestamps = self._requests[bucket]
            while timestamps and timestamps[0] <= now - window_seconds:
                timestamps.popleft()
            if len(timestamps) >= limit:
                return False
            timestamps.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()


rate_limiter: RateLimiter = InMemoryRateLimiter()


def presented_api_key(request: Request) -> str:
    """Read the key from ``X-API-Key`` or an OpenAI-style ``Authorization: Bearer``."""
    if key := request.headers.get("x-api-key"):
        return key
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


SESSION_PREFIX = "ugs_"


async def identify(request: Request, store: UsageStore | None) -> Principal | None:
    """The caller behind the presented credential, or None when there is none
    or it is not valid. Does not apply rate limits."""
    settings = get_settings()
    presented = presented_api_key(request)
    if not presented:
        return None
    if any(
        configured and hmac.compare_digest(presented, configured)
        for configured in (settings.gateway_api_key, settings.admin_api_key)
    ):
        return Principal(api_key_id=None, is_admin=True)
    if store is None:
        return None
    if presented.startswith(SESSION_PREFIX):
        user = await store.find_session(presented)
        return Principal.from_user(user) if user else None
    key = await store.find_key(presented)
    return Principal.from_key(key) if key else None


async def is_managed(store: UsageStore | None) -> bool:
    """Whether callers must present a credential.

    The gateway only runs open while there is no master key, no admin key, no
    account and no API key was ever created, so revoking every key or deleting
    every account does not quietly reopen it.
    """
    settings = get_settings()
    return bool(
        settings.gateway_api_key
        or settings.admin_api_key
        or (store is not None and (await store.has_keys() or await store.has_users()))
    )


async def authenticate(request: Request, store: UsageStore | None) -> Principal:
    """Identify the caller and apply its rate limit.

    Accepted credentials: ``GATEWAY_API_KEY`` or ``ADMIN_API_KEY`` (no
    restrictions, sees all usage), a dashboard session token from signing in,
    or an active API key created through the admin API.
    """
    principal = await identify(request, store)
    if principal is None:
        if await is_managed(store):
            raise HTTPException(
                status_code=401, detail="Sign in or provide a valid API key"
            )
        principal = Principal(api_key_id=None, is_admin=True)

    if principal.rate_limit_per_minute is not None:
        limit, window = principal.rate_limit_per_minute, 60.0
    else:
        settings = get_settings()
        limit, window = settings.rate_limit_requests, settings.rate_limit_window_seconds
    client = request.client.host if request.client else "unknown"
    if principal.api_key_id:
        bucket = f"key:{principal.api_key_id}"
    elif principal.user_id:
        bucket = f"user:{principal.user_id}"
    else:
        bucket = f"ip:{client}"
    if not rate_limiter.hit(bucket, limit=limit, window_seconds=window):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return principal


async def check_admin_access(request: Request, store: UsageStore | None) -> None:
    """Admin endpoints need an administrator account's session, ADMIN_API_KEY,
    or the master key when no admin key is set. With none of those they are
    open only until the first API key or account exists, so tenants can never
    manage keys themselves."""
    settings = get_settings()
    presented = presented_api_key(request)
    if presented.startswith(SESSION_PREFIX) and store is not None:
        user = await store.find_session(presented)
        if user is None:
            raise HTTPException(
                status_code=401, detail="Session expired; sign in again"
            )
        if not user["is_admin"]:
            raise HTTPException(status_code=403, detail="Administrators only")
        return
    expected = settings.admin_api_key or settings.gateway_api_key
    if expected:
        if not hmac.compare_digest(presented, expected):
            raise HTTPException(status_code=401, detail="Invalid admin API key")
        return
    if store is not None and (await store.has_keys() or await store.has_users()):
        raise HTTPException(
            status_code=403,
            detail="Sign in as an administrator, or set ADMIN_API_KEY "
            "(or GATEWAY_API_KEY), to manage the gateway",
        )
