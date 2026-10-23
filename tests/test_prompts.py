from tests.test_accounts import AccountEnv, bearer
from ugen_ai_gateway.api.prompt_routes import DEFAULT_SYSTEM_PROMPT
from ugen_ai_gateway.services.secret_box import PREFIX

SYSTEM_PROMPT = "Answer in Traditional Chinese."
INSTRUCTIONS = "I am a nurse; keep answers short."
QUESTION = {"role": "user", "content": "please design a system"}


def _set_prompts(env: AccountEnv, token: str) -> None:
    saved = env.client.put(
        "/v1/ugen/system-prompt", json={"text": SYSTEM_PROMPT}, headers=bearer(token)
    )
    assert saved.status_code == 200, saved.text
    saved = env.client.put(
        "/v1/ugen/auth/me/instructions",
        json={"text": f"  {INSTRUCTIONS}\n"},
        headers=bearer(token),
    )
    assert saved.json() == {"text": INSTRUCTIONS}


def _role(env: AccountEnv, token: str, name: str, prompt: str, shared=False) -> dict:
    response = env.client.post(
        "/v1/ugen/roles",
        json={"name": name, "prompt": prompt, "shared": shared},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _system(env: AccountEnv) -> str:
    first = env.cloud.calls[-1]["messages"][0]
    assert first["role"] == "system"
    return first["content"]


def test_the_default_system_prompt_names_the_assistant_kiri(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    assert env.chat(alice).status_code == 200
    assert env.cloud.calls[-1]["messages"] == [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        QUESTION,
    ]
    shown = env.client.get("/v1/ugen/system-prompt", headers=bearer(alice)).json()
    assert shown == {"text": DEFAULT_SYSTEM_PROMPT, "default": DEFAULT_SYSTEM_PROMPT}

    # Saving an empty system prompt sends none.
    env.client.put("/v1/ugen/system-prompt", json={"text": ""}, headers=bearer(alice))
    assert env.chat(alice).status_code == 200
    assert env.cloud.calls[-1]["messages"] == [QUESTION]


def test_prompts_stack_in_order_as_one_system_message(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")
    _set_prompts(env, alice)
    role = _role(env, alice, "Engineer", "Answer as a senior engineer.")

    memory = {"role": "system", "content": "Earlier: we discussed rotas."}
    response = env.chat(
        alice,
        messages=[memory, QUESTION],
        role_id=role["id"],
        conversation_prompt=" Use metric units. ",
    )
    assert response.status_code == 200
    assert env.cloud.calls[-1]["messages"] == [
        {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n"
            f"The user's custom instructions:\n{INSTRUCTIONS}\n\n"
            "Role for this conversation: Engineer\nAnswer as a senior engineer.\n\n"
            "Instructions for this conversation:\nUse metric units.\n\n"
            "Earlier: we discussed rotas.",
        },
        QUESTION,
    ]

    # Bob gets the system prompt, but not Alice's instructions or her role.
    assert env.chat(bob, role_id=role["id"]).status_code == 200
    assert env.cloud.calls[-1]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        QUESTION,
    ]


def test_api_keys_get_only_the_system_prompt(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    _set_prompts(env, alice)
    key = env.client.post(
        "/v1/ugen/keys", json={"name": "app"}, headers=bearer(alice)
    ).json()["key"]

    response = env.client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [QUESTION]},
        headers=bearer(key),
    )

    assert response.status_code == 200, response.text
    assert env.cloud.calls[-1]["messages"][0] == {
        "role": "system",
        "content": SYSTEM_PROMPT,
    }


def test_shared_roles_are_for_everyone_but_only_admins_manage_them(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")  # administrator
    bob = env.token("bob")
    shared = _role(env, alice, "Engineer", "Think like an engineer.", shared=True)
    own = _role(env, bob, "Teacher", "Explain simply.")

    listed = env.client.get("/v1/ugen/roles", headers=bearer(bob)).json()["data"]
    assert [(r["name"], r["shared"], r["mine"]) for r in listed] == [
        ("Engineer", True, False),
        ("Teacher", False, True),
    ]
    alice_sees = env.client.get("/v1/ugen/roles", headers=bearer(alice)).json()
    assert [r["name"] for r in alice_sees["data"]] == ["Engineer"]

    # Members may use a shared role, not change it or share their own.
    assert env.chat(bob, role_id=shared["id"]).status_code == 200
    assert "Role for this conversation: Engineer" in _system(env)
    change = {"name": "x", "prompt": "y"}
    url = f"/v1/ugen/roles/{shared['id']}"
    assert env.client.put(url, json=change, headers=bearer(bob)).status_code == 403
    assert env.client.delete(url, headers=bearer(bob)).status_code == 403
    sharing = {"name": "Mine", "prompt": "p", "shared": True}
    assert (
        env.client.post("/v1/ugen/roles", json=sharing, headers=bearer(bob)).status_code
        == 403
    )
    # An account cannot see or change another account's own role.
    other = f"/v1/ugen/roles/{own['id']}"
    assert env.client.put(other, json=change, headers=bearer(alice)).status_code == 404

    renamed = env.client.put(
        url,
        json={"name": "Senior engineer", "prompt": "Be precise."},
        headers=bearer(alice),
    )
    assert renamed.json()["name"] == "Senior engineer"
    assert env.client.delete(other, headers=bearer(bob)).status_code == 204
    assert env.client.get("/v1/ugen/roles").status_code == 401


def test_personal_data_in_instructions_is_redacted_like_any_text(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    env.client.put(
        "/v1/ugen/auth/me/instructions",
        json={"text": "Reply to alex@example.com"},
        headers=bearer(alice),
    )

    assert env.chat(alice).status_code == 200
    sent = _system(env)
    assert "alex@example.com" not in sent and "[EMAIL_1]" in sent


def test_instructions_and_roles_are_encrypted(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")
    _set_prompts(env, alice)
    _role(env, alice, "Nurse", "You talk to a nurse.")

    stored = env.store._connection.execute(
        "SELECT instructions FROM users WHERE username = 'alice'"
    ).fetchone()[0]
    assert stored.startswith(PREFIX) and "nurse" not in stored
    role = env.store._connection.execute("SELECT prompt FROM prompt_roles").fetchone()[
        0
    ]
    assert role.startswith(PREFIX) and "nurse" not in role
    me = "/v1/ugen/auth/me/instructions"
    assert env.client.get(me, headers=bearer(alice)).json() == {"text": INSTRUCTIONS}
    assert env.client.get(me, headers=bearer(bob)).json() == {"text": ""}
    assert env.client.get(me).status_code == 401
    assert (
        "instructions"
        not in env.client.get("/v1/ugen/auth/me", headers=bearer(alice)).json()
    )


def test_only_administrators_set_the_system_prompt(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")
    url = "/v1/ugen/system-prompt"

    assert (
        env.client.put(url, json={"text": "x"}, headers=bearer(bob)).status_code == 403
    )
    assert env.client.get(url, headers=bearer(bob)).status_code == 403
    too_long = {"text": "x" * 8001}
    assert env.client.put(url, json=too_long, headers=bearer(alice)).status_code == 422

    env.client.put(url, json={"text": SYSTEM_PROMPT}, headers=bearer(alice))
    assert env.client.get(url, headers=bearer(alice)).json()["text"] == SYSTEM_PROMPT
    # The audit log notes the change, never the prompt itself.
    details = [
        row["detail"]
        for row in env.store._connection.execute(
            "SELECT detail FROM audit WHERE event = 'system_prompt_updated'"
        )
    ]
    assert len(details) == 1 and '"characters": 30' in details[0]
    assert "Chinese" not in details[0]
