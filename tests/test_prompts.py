import re

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

    memory = {"role": "system", "content": "Earlier: we discussed rotas."}
    response = env.chat(
        alice,
        messages=[memory, QUESTION],
        conversation_prompt=" Use metric units. ",
    )
    assert response.status_code == 200
    assert env.cloud.calls[-1]["messages"] == [
        {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n"
            f"The user's custom instructions:\n{INSTRUCTIONS}\n\n"
            "Instructions for this conversation:\nUse metric units.\n\n"
            "Earlier: we discussed rotas.",
        },
        QUESTION,
    ]

    # Bob gets the system prompt, but not Alice's instructions.
    assert env.chat(bob).status_code == 200
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


def test_instructions_are_encrypted(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")
    _set_prompts(env, alice)

    stored = env.store._connection.execute(
        "SELECT instructions FROM users WHERE username = 'alice'"
    ).fetchone()[0]
    assert stored.startswith(PREFIX) and "nurse" not in stored
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


def test_name_recognition_does_not_mask_words_in_the_system_prompt(monkeypatch) -> None:
    from ugen_ai_gateway.analysis.pii import (
        PersonalDataMatch,
        clear_registered_finders,
        register_finder,
    )

    def names(text: str) -> list[PersonalDataMatch]:
        # A name finder that mistakes ordinary capitalized words for people.
        return [
            PersonalDataMatch(
                "person_name", "PERSON_NAME", m.start(), m.end(), m.group()
            )
            for m in re.finditer(r"Personality|Gateway|Alex", text)
        ]

    register_finder(names, types=["person_name"])
    try:
        env = AccountEnv(monkeypatch)
        alice = env.token("alice")
        env.client.put(
            "/v1/ugen/system-prompt",
            json={"text": "# Personality\nYou are the Gateway assistant."},
            headers=bearer(alice),
        )

        response = env.chat(
            alice,
            messages=[{"role": "user", "content": "please design a system for Alex"}],
        )

        assert response.status_code == 200
        sent = env.cloud.calls[-1]["messages"]
        system = " ".join(str(m["content"]) for m in sent if m["role"] == "system")
        user = next(m["content"] for m in sent if m["role"] == "user")
        assert "# Personality" in system and "Gateway assistant" in system
        assert "Alex" not in user and "[PERSON_NAME_" in user
    finally:
        clear_registered_finders()
