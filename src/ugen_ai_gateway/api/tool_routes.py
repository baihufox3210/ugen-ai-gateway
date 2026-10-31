"""Tools, MCP servers and agents that people define for themselves.

* A **tool** is a web address the model may call (an HTTP tool): a name, what
  it does, the parameters the model must give, and the request to send.
* An **MCP server** is registered by address; its tools are discovered and
  offered to the model as ``server__tool``.
* An **agent** is a saved role: instructions, a choice of tools, optionally a
  model and a limit on steps. Chat with one through ``agent`` in the request,
  or ``model: "agent:<name>"`` on the OpenAI-compatible API.

Everything belongs to the account that made it. Administrators can also share
items with everybody. Secrets (header values, tokens) are stored encrypted and
never returned.
"""

import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ugen_ai_gateway.api.access import audit_admin, authenticate
from ugen_ai_gateway.api.schemas import MAX_PROMPT_CHARACTERS, ChatRequest
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import get_secret_box, get_usage_store
from ugen_ai_gateway.services.keys import Principal
from ugen_ai_gateway.services.tools.builtin import BUILTIN_TOOLS
from ugen_ai_gateway.services.tools.mcp_tool import discover, tool_name
from ugen_ai_gateway.services.tools.netguard import BlockedDestination, check_url
from ugen_ai_gateway.services.tools.toolset import (
    DEFAULT_MAX_STEPS,
    MAX_STEPS,
    Toolset,
    build_toolset,
    http_spec,
    open_headers,
)
from ugen_ai_gateway.services.usage import UsageStore

router = APIRouter(prefix="/v1/ugen")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]

NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
MAX_SCHEMA_BYTES = 8000
MAX_HEADERS = 10
_PLACEHOLDER = re.compile(r"\{\w+\}")
AGENT_HEADING = "Instructions for the agent {name}:"


class HttpToolBody(BaseModel):
    name: str
    description: str = Field(default="", max_length=1000)
    url: str = Field(max_length=2000)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    # JSON schema of the arguments the model gives.
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    # A value left empty keeps the stored one of the same name.
    headers: dict[str, str | None] = Field(default_factory=dict)
    shared: bool = False


class McpServerBody(BaseModel):
    name: str
    description: str = Field(default="", max_length=1000)
    url: str = Field(max_length=2000)
    headers: dict[str, str | None] = Field(default_factory=dict)
    shared: bool = False


class AgentBody(BaseModel):
    name: str
    description: str = Field(default="", max_length=1000)
    system_prompt: str = Field(default="", max_length=MAX_PROMPT_CHARACTERS)
    # Built-in tools, HTTP tools, MCP servers or ``server__tool``.
    tools: list[str] = Field(default_factory=list, max_length=32)
    # A configured model to use; empty lets routing choose.
    model: str | None = Field(default=None, max_length=128)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=MAX_STEPS)
    shared: bool = False


class ToolTestBody(HttpToolBody):
    """A tool as typed in the editor, with the arguments to try it with."""

    # The saved tool being edited: its stored header values are used for
    # headers left empty.
    id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class RunBody(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def _bad(detail: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _need_store(store: UsageStore | None) -> UsageStore:
    if store is None:
        raise _bad("Tools need a database; set USAGE_DB_PATH", 503)
    return store


def _checked_name(name: str, kind: str) -> str:
    name = name.strip()
    if not NAME_PATTERN.match(name):
        raise _bad(
            "The name must start with a letter and use only letters, digits, "
            "'_' and '-' (40 characters at most)"
        )
    if kind != "agent" and name in BUILTIN_TOOLS:
        raise _bad(f"'{name}' is a built-in tool; pick another name")
    if kind == "mcp" and "__" in name:
        raise _bad("A server name cannot contain '__'")
    return name


def _can_edit(item: dict[str, Any], principal: Principal) -> bool:
    if item["owner_id"] is None:
        return principal.is_admin
    return item["owner_id"] == principal.user_id


def _owner_for_new(principal: Principal, shared: bool) -> str | None:
    """The owner of a new item: the account, or nobody (shared) for an
    administrator who asks for it or who has no account (an admin key)."""
    if shared:
        if not principal.is_admin:
            raise _bad("Only administrators can share with everyone", 403)
        return None
    if principal.user_id:
        return principal.user_id
    if principal.is_admin:
        return None
    raise _bad("Sign in to make tools; an API key cannot own any", 403)


async def _within_limit(store: UsageStore, owner_id: str | None) -> None:
    if await store.count_tool_items(owner_id) >= get_settings().tool_items_per_user:
        raise _bad(
            f"You can have {get_settings().tool_items_per_user} tools, servers and "
            "agents; delete one first",
            409,
        )


def _sealed_headers(
    given: dict[str, str | None], stored: dict[str, Any] | None
) -> dict[str, str]:
    if len(given) > MAX_HEADERS:
        raise _bad(f"At most {MAX_HEADERS} headers")
    box = get_secret_box()
    kept = (stored or {}).get("headers") or {}
    sealed: dict[str, str] = {}
    for name, value in given.items():
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
            raise _bad(f"'{name}' is not a valid header name")
        if value:
            sealed[name] = box.seal(value)
        elif name in kept:
            sealed[name] = kept[name]
        else:
            raise _bad(f"The header {name} needs a value")
    return sealed


def _checked_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") != "object" or not isinstance(
        schema.get("properties", {}), dict
    ):
        raise _bad("parameters must be a JSON schema of type object")
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        raise _bad("parameters are too large")
    return schema


async def _checked_url(url: str, template: bool = False) -> str:
    url = url.strip()
    probe = _PLACEHOLDER.sub("x", url) if template else url
    try:
        await check_url(probe)
    except BlockedDestination as exc:
        raise _bad(str(exc)) from exc
    return url


def _view(item: dict[str, Any], principal: Principal) -> dict[str, Any]:
    config = item["config"]
    view: dict[str, Any] = {
        "id": item["id"],
        "kind": item["kind"],
        "name": item["name"],
        "description": config.get("description", ""),
        "shared": item["owner_id"] is None,
        "editable": _can_edit(item, principal),
        "updated_at": item["updated_at"],
    }
    if item["kind"] == "http":
        view.update(
            url=config["url"],
            method=config["method"],
            parameters=config["parameters"],
            headers=sorted(config.get("headers", {})),
        )
    elif item["kind"] == "mcp":
        view.update(
            url=config["url"],
            headers=sorted(config.get("headers", {})),
            tools=[
                {
                    "name": tool_name(item["name"], tool["name"]),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters") or {},
                }
                for tool in config.get("tools", [])
            ],
        )
    else:
        view.update(
            system_prompt=config.get("system_prompt", ""),
            tools=config.get("tools", []),
            model=config.get("model"),
            max_steps=config.get("max_steps", DEFAULT_MAX_STEPS),
        )
    return view


def _known_names(items: list[dict[str, Any]]) -> set[str]:
    names = set(BUILTIN_TOOLS)
    for item in items:
        if item["kind"] == "http":
            names.add(item["name"])
        elif item["kind"] == "mcp":
            names.add(item["name"])
            names.update(
                tool_name(item["name"], tool["name"])
                for tool in item["config"].get("tools", [])
            )
    return names


async def _item(
    store: UsageStore, item_id: str, kind: str, principal: Principal
) -> dict[str, Any]:
    item = await store.get_tool_item(item_id)
    visible = item is not None and (
        item["owner_id"] is None or item["owner_id"] == principal.user_id
    )
    if not visible or item["kind"] != kind:  # type: ignore[index]
        raise _bad("Not found", 404)
    return item  # type: ignore[return-value]


async def _save(
    request: Request,
    store: UsageStore,
    principal: Principal,
    *,
    kind: str,
    name: str,
    config: dict[str, Any],
    item_id: str | None,
    shared: bool = False,
    owner_id: str | None = None,
) -> dict[str, Any]:
    if item_id is None:
        owner_id = _owner_for_new(principal, shared)
        await _within_limit(store, owner_id)
    try:
        saved = await store.save_tool_item(
            item_id=item_id, owner_id=owner_id, kind=kind, name=name, config=config
        )
    except ValueError as exc:
        raise _bad(str(exc), 409) from exc
    await audit_admin(
        request, store, f"{kind}_saved", name=name, shared=saved["owner_id"] is None
    )
    return _view(saved, principal)


@router.get("/tools")
async def catalog(store: UsageStoreDependency, request: Request) -> dict[str, Any]:
    """Everything the caller can use or edit: built-in tools, their own and
    shared HTTP tools, MCP servers and agents."""
    principal = await authenticate(request, store)
    items = await store.list_tool_items(principal.user_id) if store is not None else []
    views = [_view(item, principal) for item in items]
    return {
        "enabled": get_settings().tools_enabled,
        "writable": store is not None
        and (principal.user_id is not None or principal.is_admin),
        "builtin": [
            {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            }
            for name, tool in BUILTIN_TOOLS.items()
        ],
        "tools": [view for view in views if view["kind"] == "http"],
        "mcp": [view for view in views if view["kind"] == "mcp"],
        "agents": [view for view in views if view["kind"] == "agent"],
    }


@router.post("/tools")
async def create_tool(
    body: HttpToolBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    principal = await authenticate(request, store)
    store = _need_store(store)
    return await _save(
        request,
        store,
        principal,
        kind="http",
        name=_checked_name(body.name, "http"),
        config=await _http_config(body, None),
        item_id=None,
        shared=body.shared,
    )


@router.put("/tools/{item_id}")
async def update_tool(
    item_id: str, body: HttpToolBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    principal = await authenticate(request, store)
    store = _need_store(store)
    item = await _editable(store, item_id, "http", principal)
    return await _save(
        request,
        store,
        principal,
        kind="http",
        name=_checked_name(body.name, "http"),
        config=await _http_config(body, item["config"]),
        item_id=item_id,
        owner_id=item["owner_id"],
    )


async def _http_config(
    body: HttpToolBody, stored: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "description": body.description.strip(),
        "url": await _checked_url(body.url, template=True),
        "method": body.method,
        "parameters": _checked_schema(body.parameters),
        "headers": _sealed_headers(body.headers, stored),
    }


async def _editable(
    store: UsageStore, item_id: str, kind: str, principal: Principal
) -> dict[str, Any]:
    item = await _item(store, item_id, kind, principal)
    if not _can_edit(item, principal):
        raise _bad("Only its owner can change this", 403)
    return item


@router.delete("/tools/{item_id}")
async def delete_tool(
    item_id: str, store: UsageStoreDependency, request: Request
) -> dict[str, bool]:
    return await _delete(item_id, "http", store, request)


async def _delete(
    item_id: str, kind: str, store: UsageStore | None, request: Request
) -> dict[str, bool]:
    principal = await authenticate(request, store)
    store = _need_store(store)
    item = await _editable(store, item_id, kind, principal)
    await store.delete_tool_item(item_id)
    await audit_admin(request, store, f"{kind}_deleted", name=item["name"])
    return {"deleted": True}


async def _mcp_config(
    body: McpServerBody, stored: dict[str, Any] | None
) -> dict[str, Any]:
    url = await _checked_url(body.url)
    headers = _sealed_headers(body.headers, stored)
    plain = open_headers({"headers": headers}, get_secret_box())
    try:
        tools = await discover(url, plain)
    except BlockedDestination as exc:
        raise _bad(str(exc)) from exc
    except Exception as exc:
        raise _bad(f"Could not read tools from the server: {exc}") from exc
    return {
        "description": body.description.strip(),
        "url": url,
        "headers": headers,
        "tools": tools,
    }


@router.post("/mcp-servers")
async def create_mcp_server(
    body: McpServerBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    """Register an MCP server (streamable HTTP); its tools are read right away."""
    principal = await authenticate(request, store)
    store = _need_store(store)
    return await _save(
        request,
        store,
        principal,
        kind="mcp",
        name=_checked_name(body.name, "mcp"),
        config=await _mcp_config(body, None),
        item_id=None,
        shared=body.shared,
    )


@router.put("/mcp-servers/{item_id}")
async def update_mcp_server(
    item_id: str, body: McpServerBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    principal = await authenticate(request, store)
    store = _need_store(store)
    item = await _editable(store, item_id, "mcp", principal)
    return await _save(
        request,
        store,
        principal,
        kind="mcp",
        name=_checked_name(body.name, "mcp"),
        config=await _mcp_config(body, item["config"]),
        item_id=item_id,
        owner_id=item["owner_id"],
    )


@router.post("/mcp-servers/{item_id}/refresh")
async def refresh_mcp_server(
    item_id: str, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    """Read the server's tools again."""
    principal = await authenticate(request, store)
    store = _need_store(store)
    item = await _editable(store, item_id, "mcp", principal)
    config = item["config"]
    body = McpServerBody(
        name=item["name"],
        description=config.get("description", ""),
        url=config["url"],
        headers={name: None for name in config.get("headers", {})},
    )
    return await _save(
        request,
        store,
        principal,
        kind="mcp",
        name=item["name"],
        config=await _mcp_config(body, config),
        item_id=item_id,
        owner_id=item["owner_id"],
    )


@router.delete("/mcp-servers/{item_id}")
async def delete_mcp_server(
    item_id: str, store: UsageStoreDependency, request: Request
) -> dict[str, bool]:
    return await _delete(item_id, "mcp", store, request)


async def _agent_config(
    body: AgentBody, store: UsageStore, principal: Principal
) -> dict[str, Any]:
    items = await store.list_tool_items(principal.user_id)
    unknown = [name for name in body.tools if name not in _known_names(items)]
    if unknown:
        raise _bad(f"Unknown tools: {', '.join(unknown)}")
    return {
        "description": body.description.strip(),
        "system_prompt": body.system_prompt.strip(),
        "tools": list(dict.fromkeys(body.tools)),
        "model": (body.model or "").strip() or None,
        "max_steps": body.max_steps,
    }


@router.post("/agents")
async def create_agent(
    body: AgentBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    principal = await authenticate(request, store)
    store = _need_store(store)
    return await _save(
        request,
        store,
        principal,
        kind="agent",
        name=_checked_name(body.name, "agent"),
        config=await _agent_config(body, store, principal),
        item_id=None,
        shared=body.shared,
    )


@router.put("/agents/{item_id}")
async def update_agent(
    item_id: str, body: AgentBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    principal = await authenticate(request, store)
    store = _need_store(store)
    item = await _editable(store, item_id, "agent", principal)
    return await _save(
        request,
        store,
        principal,
        kind="agent",
        name=_checked_name(body.name, "agent"),
        config=await _agent_config(body, store, principal),
        item_id=item_id,
        owner_id=item["owner_id"],
    )


@router.delete("/agents/{item_id}")
async def delete_agent(
    item_id: str, store: UsageStoreDependency, request: Request
) -> dict[str, bool]:
    return await _delete(item_id, "agent", store, request)


@router.post("/tools/run")
async def run_tool(
    body: RunBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    """Try a tool once, with the arguments given, as a model would call it."""
    principal = await authenticate(request, store)
    toolset = await _toolset(store, principal, [body.name])
    outcome = await toolset.call(body.name, json.dumps(body.arguments))
    return {"ok": outcome.ok, "result": outcome.text, "ms": round(outcome.ms, 1)}


@router.post("/tools/test")
async def test_tool(
    body: ToolTestBody, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    """Try an HTTP tool as typed in the editor, before it is saved."""
    principal = await authenticate(request, store)
    if not get_settings().tools_enabled:
        raise _bad("Tools are turned off on this gateway", 403)
    if principal.user_id is None and not principal.is_admin:
        raise _bad("Sign in to make tools; an API key cannot own any", 403)
    stored = None
    if body.id and store is not None:
        stored = (await _item(store, body.id, "http", principal))["config"]
    config = await _http_config(body, stored)
    spec = http_spec(
        {"name": body.name.strip() or "test", "config": config}, get_secret_box()
    )
    outcome = await Toolset({spec.name: spec}).call(
        spec.name, json.dumps(body.arguments)
    )
    return {"ok": outcome.ok, "result": outcome.text, "ms": round(outcome.ms, 1)}


async def _toolset(
    store: UsageStore | None,
    principal: Principal,
    names: list[str],
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    agent_name: str | None = None,
    strict: bool = True,
) -> Toolset:
    if not get_settings().tools_enabled:
        raise _bad("Tools are turned off on this gateway", 403)
    items = await store.list_tool_items(principal.user_id) if store else []
    # The caller's own items win over shared ones of the same name.
    items.sort(key=lambda item: item["owner_id"] is None)
    toolset, missing = build_toolset(
        names,
        items,
        get_secret_box(),
        max_steps=max_steps,
        agent_name=agent_name,
    )
    if missing and strict:
        raise _bad(f"Unknown tools: {', '.join(missing)}", 404)
    return toolset


async def with_tools(
    request: ChatRequest, store: UsageStore | None, principal: Principal
) -> ChatRequest:
    """``request`` ready to run with the tools it asks for: an agent adds its
    instructions, its model and its tools; ``tools`` adds more."""
    if not request.agent and not request.tools:
        return request
    names = list(request.tools)
    update: dict[str, Any] = {}
    max_steps = DEFAULT_MAX_STEPS
    agent_name = None
    if request.agent:
        if store is None:
            raise _bad("Agents need a database; set USAGE_DB_PATH", 503)
        items = await store.list_tool_items(principal.user_id, "agent")
        items.sort(key=lambda item: item["owner_id"] is None)
        agent = next(
            (item for item in items if request.agent in (item["id"], item["name"])),
            None,
        )
        if agent is None:
            raise _bad(f"The agent '{request.agent}' does not exist", 404)
        config = agent["config"]
        agent_name = agent["name"]
        names = list(dict.fromkeys([*config.get("tools", []), *names]))
        max_steps = int(config.get("max_steps", DEFAULT_MAX_STEPS))
        prompt = config.get("system_prompt", "")
        if prompt:
            update["messages"] = _with_agent_prompt(request, agent_name, prompt)
        if config.get("model") and request.model is None:
            update["model"] = config["model"]
    toolset = await _toolset(
        store,
        principal,
        names,
        max_steps=max_steps,
        agent_name=agent_name,
        strict=not request.agent,  # a tool the agent lost is skipped
    )
    update["toolset"] = toolset or None
    return request.model_copy(update=update)


def _with_agent_prompt(request: ChatRequest, name: str, prompt: str) -> list[Any]:
    """The agent's instructions as a system message after the other system ones."""
    messages = list(request.messages)
    position = 0
    while position < len(messages) and messages[position].get("role") == "system":
        position += 1
    messages.insert(
        position,
        {
            "role": "system",
            "content": f"{AGENT_HEADING.format(name=name)}\n{prompt}",
        },
    )
    return messages
