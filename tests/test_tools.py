import asyncio
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import uvicorn
from openai.types.chat import ChatCompletion

from tests.test_accounts import AccountEnv, bearer
from ugen_ai_gateway.config import Settings, get_settings
from ugen_ai_gateway.services.tools.builtin import calculate
from ugen_ai_gateway.services.tools.netguard import (
    BlockedDestination,
    check_url,
    host_is_allowed,
)


@pytest.fixture
def local_hosts(monkeypatch) -> Iterator[None]:
    """Let tools reach this machine, where the test servers run."""
    monkeypatch.setenv("TOOL_ALLOWED_HOSTS", "127.0.0.1,localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_GET(self) -> None:
        self.seen.append({"path": self.path, "token": self.headers.get("X-Token")})
        body = b"Contact: bob@example.com, order 42"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def web() -> Iterator[str]:
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def mcp_url() -> Iterator[str]:
    """The example server of examples/mcp_demo_server.py."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))
    try:
        from mcp_demo_server import app
    finally:
        sys.path.pop(0)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    runner = uvicorn.Server(
        uvicorn.Config(app(), host="127.0.0.1", port=port, log_level="error")
    )
    threading.Thread(target=runner.run, daemon=True).start()
    while not runner.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    runner.should_exit = True


def completion(content: str | None = None, calls: list[tuple[str, str]] | None = None):
    tool_calls = [
        {
            "id": f"call_{position}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
        for position, (name, arguments) in enumerate(calls or [])
    ]
    return ChatCompletion.model_validate(
        {
            "id": "cmpl",
            "object": "chat.completion",
            "created": 0,
            "model": "cloud-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls or None,
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
    )


class Script:
    """A model that answers with the given completions, one per call."""

    def __init__(self, env: AccountEnv, *steps: ChatCompletion) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []
        env.cloud.complete = self.complete  # type: ignore[method-assign]
        env.local.complete = self.complete  # type: ignore[method-assign]

    async def complete(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        return self.steps.pop(0)


def make(env: AccountEnv, token: str, path: str, **body: Any) -> Any:
    return env.client.post(f"/v1/ugen/{path}", json=body, headers=bearer(token))


# -- the tools themselves -------------------------------------------------


def test_the_calculator_does_arithmetic_and_nothing_else() -> None:
    assert calculate("2 + 3 * 4") == 14
    assert calculate("sqrt(16) + 2 ** 3") == 12
    for bad in ("__import__('os').system('id')", "open('x')", "1; 2", "9 ** 99999"):
        with pytest.raises((ValueError, SyntaxError)):
            calculate(bad)


def test_tools_cannot_reach_private_addresses() -> None:
    async def blocked(url: str) -> None:
        with pytest.raises(BlockedDestination):
            await check_url(url)

    for url in (
        "http://127.0.0.1/",
        "http://10.1.2.3:8080/x",
        "http://192.168.0.5/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://user:pass@93.184.216.34/",
    ):
        asyncio.run(blocked(url))
    asyncio.run(check_url("http://93.184.216.34/"))  # a public address is fine


def test_the_administrator_can_allow_a_private_host(local_hosts) -> None:
    asyncio.run(check_url("http://127.0.0.1:9/"))
    assert host_is_allowed("localhost")
    assert not host_is_allowed("evil.example.com")
    assert host_is_allowed("api.corp.example.com", ["*.corp.example.com"])


def test_a_private_host_is_refused_when_saving_a_tool(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    saved = make(
        env,
        alice,
        "tools",
        name="meta",
        url="http://169.254.169.254/latest",
        method="GET",
    )

    assert saved.status_code == 400
    assert "TOOL_ALLOWED_HOSTS" in saved.json()["detail"]


# -- HTTP tools through the API -------------------------------------------


def test_an_http_tool_is_made_run_and_kept_private(
    monkeypatch, local_hosts, web
) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")

    created = make(
        env,
        alice,
        "tools",
        name="orders",
        description="Look an order up",
        url=f"{web}/orders/{{order_id}}",
        method="GET",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "verbose": {"type": "string"},
            },
            "required": ["order_id"],
        },
        headers={"X-Token": "s3cret"},
    )
    assert created.status_code == 200, created.text
    tool = created.json()
    assert tool["headers"] == ["X-Token"] and "s3cret" not in created.text
    assert tool["editable"] is True and tool["shared"] is False

    # The secret is stored encrypted.
    stored = asyncio.run(env.store.list_tool_items(None))
    assert stored == []  # not shared
    raw = env.store._connection.execute("SELECT config FROM tool_items").fetchone()[0]
    assert "s3cret" not in raw

    ran = make(
        env,
        alice,
        "tools/run",
        name="orders",
        arguments={"order_id": "A 1", "verbose": "yes"},
    )
    assert ran.json()["ok"] is True
    assert "order 42" in ran.json()["result"]
    assert _Handler.seen[-1] == {"path": "/orders/A%201?verbose=yes", "token": "s3cret"}

    # Bob sees nothing of it and cannot use, change or delete it.
    catalog = env.client.get("/v1/ugen/tools", headers=bearer(bob)).json()
    assert catalog["tools"] == []
    assert make(env, bob, "tools/run", name="orders").status_code == 404
    assert (
        env.client.delete(
            f"/v1/ugen/tools/{tool['id']}", headers=bearer(bob)
        ).status_code
        == 404
    )

    # Editing without retyping the secret keeps it.
    edited = env.client.put(
        f"/v1/ugen/tools/{tool['id']}",
        json={
            "name": "orders",
            "url": f"{web}/orders/{{order_id}}",
            "headers": {"X-Token": ""},
        },
        headers=bearer(alice),
    )
    assert edited.status_code == 200
    make(env, alice, "tools/run", name="orders", arguments={"order_id": "7"})
    assert _Handler.seen[-1]["token"] == "s3cret"

    deleted = env.client.delete(f"/v1/ugen/tools/{tool['id']}", headers=bearer(alice))
    assert deleted.json() == {"deleted": True}
    assert make(env, alice, "tools/run", name="orders").status_code == 404


def test_a_tool_can_be_tried_before_it_is_saved(monkeypatch, local_hosts, web) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")
    draft = {
        "name": "orders",
        "url": f"{web}/orders/{{order_id}}",
        "headers": {"X-Token": "typed"},
        "arguments": {"order_id": "9"},
    }

    tried = make(env, alice, "tools/test", **draft)

    assert tried.status_code == 200, tried.text
    assert tried.json()["ok"] is True and "order 42" in tried.json()["result"]
    assert _Handler.seen[-1] == {"path": "/orders/9", "token": "typed"}
    assert (
        make(
            env, alice, "tools/test", **{**draft, "url": "http://10.0.0.1/x"}
        ).status_code
        == 400
    )
    # Nothing was saved.
    assert env.client.get("/v1/ugen/tools", headers=bearer(alice)).json()["tools"] == []

    # Editing a saved tool: a header left empty uses the stored secret.
    saved = make(
        env, alice, "tools", name="orders", url=f"{web}/o", headers={"X-Token": "kept"}
    )
    again = make(
        env,
        alice,
        "tools/test",
        **{**draft, "id": saved.json()["id"], "headers": {"X-Token": ""}},
    )
    assert again.json()["ok"] is True
    assert _Handler.seen[-1]["token"] == "kept"
    # Somebody else's tool is not theirs to borrow secrets from.
    stolen = make(
        env,
        bob,
        "tools/test",
        **{**draft, "id": saved.json()["id"], "headers": {"X-Token": ""}},
    )
    assert stolen.status_code == 404


def test_mcp_tool_schemas_are_listed_for_the_trying_forms(
    monkeypatch, local_hosts, mcp_url
) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    make(env, alice, "mcp-servers", name="demo", url=mcp_url)

    server = env.client.get("/v1/ugen/tools", headers=bearer(alice)).json()["mcp"][0]

    word_count = next(t for t in server["tools"] if t["name"] == "demo__word_count")
    assert word_count["parameters"]["properties"]["text"]["type"] == "string"


def test_names_are_checked_and_unique(monkeypatch, local_hosts, web) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    body = {"url": f"{web}/x"}

    assert make(env, alice, "tools", name="bad name", **body).status_code == 400
    assert make(env, alice, "tools", name="calculator", **body).status_code == 400
    assert make(env, alice, "tools", name="ok", **body).status_code == 200
    assert make(env, alice, "tools", name="ok", **body).status_code == 409
    wrong = make(env, alice, "tools", name="p", parameters={"type": "string"}, **body)
    assert wrong.status_code == 400


def test_an_administrator_can_share_with_everyone(
    monkeypatch, local_hosts, web
) -> None:
    env = AccountEnv(monkeypatch)
    admin = env.token("alice")
    bob = env.token("bob")

    assert (
        make(env, bob, "tools", name="t", url=f"{web}/x", shared=True).status_code
        == 403
    )
    shared = make(env, admin, "tools", name="status", url=f"{web}/x", shared=True)
    assert shared.status_code == 200 and shared.json()["shared"] is True

    seen = env.client.get("/v1/ugen/tools", headers=bearer(bob)).json()["tools"]
    assert [tool["name"] for tool in seen] == ["status"]
    assert seen[0]["editable"] is False
    assert make(env, bob, "tools/run", name="status").json()["ok"] is True
    assert (
        env.client.delete(
            f"/v1/ugen/tools/{shared.json()['id']}", headers=bearer(bob)
        ).status_code
        == 403
    )


def test_the_number_of_items_is_limited(monkeypatch, local_hosts, web) -> None:
    monkeypatch.setenv("TOOL_ITEMS_PER_USER", "2")
    get_settings.cache_clear()
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    assert make(env, alice, "tools", name="a", url=f"{web}/x").status_code == 200
    assert make(env, alice, "agents", name="b").status_code == 200
    assert make(env, alice, "tools", name="c", url=f"{web}/x").status_code == 409


def test_tools_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        env = AccountEnv(monkeypatch)
        alice = env.token("alice")
        assert (
            env.client.get("/v1/ugen/tools", headers=bearer(alice)).json()["enabled"]
            is False
        )
        assert make(env, alice, "tools/run", name="calculator").status_code == 403
        response = env.chat(alice, tools=["calculator"])
        assert response.status_code == 403
    finally:
        get_settings.cache_clear()


# -- agents ----------------------------------------------------------------


def test_an_agent_calls_tools_and_the_model_never_sees_personal_data(
    monkeypatch, local_hosts, web
) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    make(env, alice, "tools", name="orders", description="Orders", url=f"{web}/o")
    agent = make(
        env,
        alice,
        "agents",
        name="helper",
        system_prompt="You help with orders.",
        tools=["orders", "calculator"],
        max_steps=3,
    )
    assert agent.status_code == 200, agent.text
    script = Script(
        env,
        completion(calls=[("orders", "{}"), ("calculator", '{"expression": "6*7"}')]),
        completion("Done: 42."),
    )

    response = env.chat(alice, agent="helper")

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "Done: 42."
    first, second = script.calls
    assert [tool["function"]["name"] for tool in first["tools"]] == [
        "orders",
        "calculator",
    ]
    system = [m["content"] for m in first["messages"] if m["role"] == "system"]
    assert any("You help with orders." in text for text in system)
    tool_messages = [m for m in second["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_0", "call_1"]
    assert tool_messages[1]["content"] == "42"
    # What the order tool returned had an e-mail address: the model got a placeholder.
    assert "bob@example.com" not in tool_messages[0]["content"]
    assert "[EMAIL_" in tool_messages[0]["content"]
    assert "bob@example.com" not in json.dumps(second["messages"])
    # Token counts add up over the steps.
    assert response.json()["usage"]["generation"]["total_tokens"] == 14


def test_tool_events_are_streamed_and_audited(monkeypatch, local_hosts, web) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    Script(
        env,
        completion(calls=[("calculator", '{"expression": "1+1"}')]),
        completion("It is 2."),
    )

    streamed = env.client.post(
        "/v1/ugen/chat/stream",
        json={
            "messages": [{"role": "user", "content": "please design a system"}],
            "tools": ["calculator"],
        },
        headers=bearer(alice),
    )

    events = [json.loads(line) for line in streamed.text.splitlines() if line]
    kinds = [event["type"] for event in events]
    assert kinds.index("tool_call") < kinds.index("tool_result") < kinds.index("delta")
    call = next(e for e in events if e["type"] == "tool_call")
    result = next(e for e in events if e["type"] == "tool_result")
    assert call["name"] == "calculator" and json.loads(call["arguments"]) == {
        "expression": "1+1"
    }
    assert result["ok"] is True and result["content"] == "2"
    assert (
        events[-1]["type"] == "result" and events[-1]["data"]["content"] == "It is 2."
    )

    audit = [
        e
        for e in asyncio.run(env.store.audit_entries(limit=50))
        if e["event"] == "tool.call"
    ]
    assert len(audit) == 1
    assert (
        audit[0]["detail"]["tool"] == "calculator" and audit[0]["detail"]["ok"] is True
    )
    assert "expression" not in json.dumps(audit[0]["detail"])


def test_a_failing_tool_is_reported_to_the_model_not_raised(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    script = Script(
        env,
        completion(calls=[("calculator", '{"expression": "1/0"}')]),
        completion("I could not divide."),
    )

    response = env.chat(alice, tools=["calculator"])

    assert response.status_code == 200
    tool_message = next(m for m in script.calls[1]["messages"] if m["role"] == "tool")
    assert tool_message["content"].startswith("The tool failed")


def test_the_model_must_answer_when_the_steps_run_out(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    again = completion(calls=[("calculator", '{"expression": "1"}')])
    script = Script(env, again, again, completion("Enough."))
    make(env, alice, "agents", name="short", tools=["calculator"], max_steps=2)

    response = env.chat(alice, agent="short")

    assert response.json()["content"] == "Enough."
    assert [("tools" in call) for call in script.calls] == [True, True, False]


def test_unknown_agents_and_tools_are_rejected(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    assert env.chat(alice, agent="ghost").status_code == 404
    assert env.chat(alice, tools=["ghost"]).status_code == 404
    unknown = make(env, alice, "agents", name="a", tools=["ghost"])
    assert unknown.status_code == 400 and "ghost" in unknown.json()["detail"]


def test_an_agent_is_private_to_its_owner(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")
    make(env, alice, "agents", name="mine")

    assert env.chat(bob, agent="mine").status_code == 404
    assert env.client.get("/v1/ugen/tools", headers=bearer(bob)).json()["agents"] == []


def test_the_openai_api_runs_an_agent_by_model_name(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    make(env, alice, "agents", name="calc", tools=["calculator"])
    Script(
        env,
        completion(calls=[("calculator", '{"expression": "2*21"}')]),
        completion("42"),
    )

    models = env.client.get("/v1/models", headers=bearer(alice)).json()["data"]
    assert "agent:calc" in [model["id"] for model in models]
    answered = env.client.post(
        "/v1/chat/completions",
        json={
            "model": "agent:calc",
            "messages": [{"role": "user", "content": "please design a system"}],
        },
        headers=bearer(alice),
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["choices"][0]["message"]["content"] == "42"
    missing = env.client.post(
        "/v1/chat/completions",
        json={"model": "agent:nope", "messages": [{"role": "user", "content": "hi"}]},
        headers=bearer(alice),
    )
    assert missing.status_code == 404


# -- MCP -------------------------------------------------------------------


def test_an_mcp_server_offers_its_tools(monkeypatch, local_hosts, mcp_url) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    created = make(
        env, alice, "mcp-servers", name="demo", url=mcp_url, headers={"X-Key": "k"}
    )
    assert created.status_code == 200, created.text
    server = created.json()
    assert [tool["name"] for tool in server["tools"]] == [
        "demo__word_count",
        "demo__reverse_text",
        "demo__convert_temperature",
    ]
    assert server["headers"] == ["X-Key"] and '"k"' not in created.text

    ran = make(
        env,
        alice,
        "tools/run",
        name="demo__word_count",
        arguments={"text": "one two three"},
    )
    assert ran.json() == {"ok": True, "result": "3", "ms": ran.json()["ms"]}
    warm = make(
        env,
        alice,
        "tools/run",
        name="demo__convert_temperature",
        arguments={"value": 100, "to": "fahrenheit"},
    )
    assert warm.json()["result"] == "212.0 °F"

    # An agent can take a whole server; a model calls its tools by full name.
    make(env, alice, "agents", name="adder", tools=["demo"])
    script = Script(
        env,
        completion(calls=[("demo__word_count", '{"text": "a b c d"}')]),
        completion("It is 4."),
    )
    response = env.chat(alice, agent="adder")
    assert response.json()["content"] == "It is 4."
    names = [tool["function"]["name"] for tool in script.calls[0]["tools"]]
    assert "demo__word_count" in names
    tool_message = next(m for m in script.calls[1]["messages"] if m["role"] == "tool")
    assert tool_message["content"] == "4"

    refreshed = env.client.post(
        f"/v1/ugen/mcp-servers/{server['id']}/refresh", headers=bearer(alice)
    )
    assert refreshed.status_code == 200
    assert (
        env.client.delete(
            f"/v1/ugen/mcp-servers/{server['id']}", headers=bearer(alice)
        ).status_code
        == 200
    )


def test_an_mcp_server_that_cannot_be_reached_is_not_saved(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    private = make(
        env, alice, "mcp-servers", name="local", url="http://127.0.0.1:9/mcp"
    )
    assert (
        private.status_code == 400 and "TOOL_ALLOWED_HOSTS" in private.json()["detail"]
    )
    assert (
        make(
            env, alice, "mcp-servers", name="x__y", url="http://93.184.216.34/"
        ).status_code
        == 400
    )


def test_settings_have_safe_defaults() -> None:
    settings = Settings()
    assert settings.tools_enabled is True
    assert settings.tool_allowed_hosts == ""
