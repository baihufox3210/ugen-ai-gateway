"""MCP servers (Model Context Protocol) over streamable HTTP.

A person registers a server's address, and optionally a token; its tools are
listed once and kept with the registration, and each call opens a short
connection of its own. Servers that run as local programs (stdio) are not
supported: that would let an account run commands on this machine.
"""

import re
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.services.tools.netguard import check_url

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
MAX_TOOL_NAME = 64


def tool_name(server: str, tool: str) -> str:
    """The name a model sees for a tool of a server: ``server__tool``."""
    return _UNSAFE.sub("_", f"{server}__{tool}")[:MAX_TOOL_NAME]


class _Connection:
    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self._url = url
        self._headers = headers

    async def __aenter__(self) -> ClientSession:
        await check_url(self._url)
        timeout = get_settings().tool_timeout_seconds
        self._http = httpx2.AsyncClient(
            headers=self._headers, timeout=httpx2.Timeout(timeout)
        )
        self._transport = streamable_http_client(self._url, http_client=self._http)
        try:
            read, write = await self._transport.__aenter__()
            self._session = ClientSession(read, write, read_timeout_seconds=timeout)
            await self._session.__aenter__()
            await self._session.initialize()
        except BaseException:
            await self._http.aclose()
            raise
        return self._session

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self._session.__aexit__(*exc)
            await self._transport.__aexit__(*exc)
        finally:
            await self._http.aclose()


def connect(url: str, headers: dict[str, str]) -> _Connection:
    return _Connection(url, headers)


async def discover(url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """The tools a server offers: name, description and input schema."""
    async with connect(url, headers) as session:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(
                params=None if cursor is None else _page(cursor)
            )
            tools.extend(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema
                    or {"type": "object", "properties": {}},
                }
                for tool in page.tools
            )
            cursor = page.next_cursor
            if not cursor or len(tools) >= 100:
                return tools[:100]


def _page(cursor: str) -> Any:
    from mcp import types

    return types.PaginatedRequestParams(cursor=cursor)


def _result_text(result: Any) -> str:
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        parts.append(text if isinstance(text, str) else f"[{block.type} content]")
    return "\n".join(parts)


async def call_mcp_tool(
    url: str, headers: dict[str, str], tool: str, arguments: dict[str, Any]
) -> str:
    async with connect(url, headers) as session:
        result = await session.call_tool(tool, arguments)
    text = _result_text(result)
    if getattr(result, "is_error", False):
        raise ValueError(text or "The tool reported an error")
    return text
