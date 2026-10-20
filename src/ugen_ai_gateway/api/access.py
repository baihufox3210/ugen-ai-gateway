import hmac
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

from ugen_ai_gateway.config import get_settings

_requests_by_client: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = Lock()


def presented_api_key(request: Request) -> str:
    """Read the key from ``X-API-Key`` or an OpenAI-style ``Authorization: Bearer``."""
    if key := request.headers.get("x-api-key"):
        return key
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def check_api_access(request: Request) -> None:
    settings = get_settings()
    if settings.gateway_api_key and not hmac.compare_digest(
        presented_api_key(request), settings.gateway_api_key
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


def check_admin_access(request: Request) -> None:
    """Audit endpoints need ADMIN_API_KEY when it is set, else the gateway key."""
    settings = get_settings()
    if not settings.admin_api_key:
        check_api_access(request)
        return
    if not hmac.compare_digest(presented_api_key(request), settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin API key")
