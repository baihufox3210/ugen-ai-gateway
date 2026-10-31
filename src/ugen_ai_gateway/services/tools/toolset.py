"""The tools one request may call, and how a call is run.

A ``Toolset`` is built from names: built-in tools, HTTP tools and MCP servers
that the caller owns or that administrators share. A call never raises: a
failure comes back as text the model can read and react to.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.services.secret_box import SecretBox
from ugen_ai_gateway.services.tools.builtin import BUILTIN_TOOLS
from ugen_ai_gateway.services.tools.http_tool import call_http_tool
from ugen_ai_gateway.services.tools.mcp_tool import call_mcp_tool, tool_name
from ugen_ai_gateway.services.tools.netguard import BlockedDestination

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 6
MAX_STEPS = 12
EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]
    # builtin | http | mcp
    source: str


@dataclass
class ToolOutcome:
    ok: bool
    text: str
    ms: float


@dataclass
class Toolset:
    """What a model may call during one request."""

    specs: dict[str, ToolSpec] = field(default_factory=dict)
    max_steps: int = DEFAULT_MAX_STEPS
    # The agent whose instructions and tools these are, when there is one.
    agent_name: str | None = None

    def __bool__(self) -> bool:
        return bool(self.specs)

    def schemas(self) -> list[dict[str, Any]]:
        """The tools in the form chat models take them."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description[:1000],
                    "parameters": spec.parameters or EMPTY_SCHEMA,
                },
            }
            for spec in self.specs.values()
        ]

    async def call(self, name: str, raw_arguments: str) -> ToolOutcome:
        started = time.perf_counter()
        settings = get_settings()

        def outcome(ok: bool, text: str) -> ToolOutcome:
            if len(text) > settings.tool_max_result_chars:
                text = text[: settings.tool_max_result_chars] + "\n… (shortened)"
            return ToolOutcome(ok, text, (time.perf_counter() - started) * 1000)

        spec = self.specs.get(name)
        if spec is None:
            return outcome(False, f"There is no tool named {name}.")
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise TypeError("The arguments must be a JSON object")
        except (ValueError, TypeError) as exc:
            return outcome(False, f"The arguments were not valid: {exc}")
        try:
            text = await asyncio.wait_for(
                spec.run(arguments), timeout=settings.tool_timeout_seconds * 2
            )
            return outcome(True, text)
        except BlockedDestination as exc:
            return outcome(False, f"Blocked: {exc}")
        except TimeoutError:
            return outcome(False, "The tool took too long.")
        except Exception as exc:  # noqa: BLE001 - any failure goes back to the model
            logger.info("Tool %s failed: %s", name, exc)
            return outcome(False, f"The tool failed: {exc}")


def open_headers(config: dict[str, Any], box: SecretBox) -> dict[str, str]:
    """Headers of a stored tool or server, with their values unsealed."""
    return {
        str(name): box.open(str(value))
        for name, value in (config.get("headers") or {}).items()
    }


def http_spec(item: dict[str, Any], box: SecretBox) -> ToolSpec:
    config = item["config"]
    headers = open_headers(config, box)

    async def run(arguments: dict[str, Any]) -> str:
        return await call_http_tool(config, headers, arguments)

    return ToolSpec(
        item["name"],
        config.get("description", ""),
        config.get("parameters") or EMPTY_SCHEMA,
        run,
        "http",
    )


def _mcp_specs(
    item: dict[str, Any], box: SecretBox, only: set[str] | None = None
) -> list[ToolSpec]:
    config = item["config"]
    headers = open_headers(config, box)
    url = str(config["url"])
    specs = []
    for tool in config.get("tools", []):
        full_name = tool_name(item["name"], tool["name"])
        if only is not None and full_name not in only:
            continue

        async def run(arguments: dict[str, Any], remote: str = tool["name"]) -> str:
            return await call_mcp_tool(url, headers, remote, arguments)

        specs.append(
            ToolSpec(
                full_name,
                tool.get("description", ""),
                tool.get("parameters") or EMPTY_SCHEMA,
                run,
                "mcp",
            )
        )
    return specs


def _builtin_spec(name: str) -> ToolSpec:
    tool = BUILTIN_TOOLS[name]
    return ToolSpec(
        name, tool["description"], tool["parameters"], tool["run"], "builtin"
    )


def build_toolset(
    names: list[str],
    items: list[dict[str, Any]],
    box: SecretBox,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    agent_name: str | None = None,
) -> tuple[Toolset, list[str]]:
    """The toolset for ``names`` and the names that matched nothing.

    A name is a built-in tool, an HTTP tool, an MCP server (all its tools) or
    one tool of a server (``server__tool``). Items are ordered with the
    caller's own first, so they win over a shared one of the same name."""
    if not get_settings().tools_enabled:
        return Toolset(max_steps=max_steps, agent_name=agent_name), list(names)
    http = {}
    servers = {}
    for item in items:
        pool = (
            http
            if item["kind"] == "http"
            else servers
            if item["kind"] == "mcp"
            else None
        )
        if pool is not None:
            pool.setdefault(item["name"], item)
    all_mcp = {
        spec.name: (item, spec)
        for item in servers.values()
        for spec in _mcp_specs(item, box)
    }
    specs: dict[str, ToolSpec] = {}
    missing = []
    for name in names:
        if name in BUILTIN_TOOLS:
            specs[name] = _builtin_spec(name)
        elif name in http:
            specs[name] = http_spec(http[name], box)
        elif name in servers:
            for spec in _mcp_specs(servers[name], box):
                specs[spec.name] = spec
        elif name in all_mcp:
            specs[name] = all_mcp[name][1]
        else:
            missing.append(name)
    return Toolset(specs, max(1, min(max_steps, MAX_STEPS)), agent_name), missing
