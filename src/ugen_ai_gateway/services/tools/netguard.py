"""Keeps tools from reaching this machine's own network.

Anyone with an account can point an HTTP tool or an MCP server at a URL, so
every outgoing request is checked first: only http(s), and the host must
resolve to public addresses. Loopback, private, link-local (cloud metadata
services live there) and other special ranges are refused unless the
administrator lists the host in ``TOOL_ALLOWED_HOSTS``.

``GuardedTransport`` makes the check at the moment of connecting, on the very
address that is then used, so a name that answers differently a second time
(DNS rebinding) cannot slip past it.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from ugen_ai_gateway.config import get_settings


class BlockedDestination(Exception):
    """The address a tool tried to reach is not allowed."""


def allowed_hosts() -> list[str]:
    raw = get_settings().tool_allowed_hosts
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def host_is_allowed(host: str, allowed: Iterable[str] | None = None) -> bool:
    """Whether the administrator listed ``host`` (``name`` or ``*.suffix``)."""
    host = host.lower().rstrip(".")
    for pattern in allowed_hosts() if allowed is None else allowed:
        if pattern == host or (pattern.startswith("*.") and host.endswith(pattern[1:])):
            return True
    return False


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """The addresses ``host`` may be reached at; raises when none is allowed."""
    try:
        found = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise BlockedDestination(f"Cannot resolve {host}") from exc
    addresses = list(dict.fromkeys(str(item[4][0]) for item in found))
    if not addresses:
        raise BlockedDestination(f"Cannot resolve {host}")
    if host_is_allowed(host):
        return addresses
    for address in addresses:
        if not _public(address):
            raise BlockedDestination(
                f"{host} is on a private or internal address; ask an administrator "
                "to add it to TOOL_ALLOWED_HOSTS"
            )
    return addresses


async def check_url(url: str) -> None:
    """Raise ``BlockedDestination`` unless ``url`` may be requested."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedDestination("Only http:// and https:// addresses can be used")
    if parts.username or parts.password:
        raise BlockedDestination("Put credentials in headers, not in the address")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    await resolve(parts.hostname, port)


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await resolve(host, port)
        return await self._inner.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise BlockedDestination("Unix sockets cannot be used")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_client(
    timeout: float, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    """An HTTP client that connects only to allowed addresses and does not
    follow redirects (a tool follows them itself, checking each hop)."""
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(
        network_backend=_GuardedBackend(), max_connections=10
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        headers=headers,
        follow_redirects=False,
        trust_env=False,
    )
