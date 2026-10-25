from tests.test_accounts import AccountEnv, bearer

TEXT = "please design a system and email alex@example.com or call 0912-345-678"
MESSAGES = [{"role": "user", "content": TEXT}]


def _sent(env: AccountEnv) -> str:
    return " ".join(str(m["content"]) for m in env.cloud.calls[-1]["messages"])


def _choose(env: AccountEnv, token: str, off: list[str]):
    return env.client.put(
        "/v1/ugen/privacy/redaction", json={"off": off}, headers=bearer(token)
    )


def test_everything_is_redacted_until_an_account_switches_a_type_off(
    monkeypatch,
) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    bob = env.token("bob")

    shown = env.client.get("/v1/ugen/privacy/redaction", headers=bearer(alice)).json()
    assert (
        shown["off"] == [] and "email" in shown["types"] and "other" in shown["types"]
    )

    assert env.chat(alice, messages=MESSAGES).status_code == 200
    sent = _sent(env)
    assert "alex@example.com" not in sent and "0912-345-678" not in sent

    assert _choose(env, alice, ["email"]).json()["off"] == ["email"]
    assert env.chat(alice, messages=MESSAGES).status_code == 200
    sent = _sent(env)
    assert "alex@example.com" in sent and "0912-345-678" not in sent

    # Another account keeps the default.
    assert env.chat(bob, messages=MESSAGES).status_code == 200
    assert "alex@example.com" not in _sent(env)

    assert _choose(env, alice, ["nonsense"]).status_code == 422
    assert env.client.get("/v1/ugen/privacy/redaction").status_code == 401


def test_the_result_lists_what_was_replaced(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")

    routing = env.chat(alice, messages=MESSAGES).json()["routing"]

    assert {r["type"] for r in routing["redactions"]} >= {"email", "taiwan_mobile"}
    by_type = {r["type"]: r for r in routing["redactions"]}
    assert by_type["email"]["value"] == "alex@example.com"
    assert by_type["email"]["placeholder"].startswith("[EMAIL_")
    assert routing["redacted_values"] == len(routing["redactions"])


def test_api_keys_have_their_own_choice_and_do_not_echo_values(monkeypatch) -> None:
    env = AccountEnv(monkeypatch)
    alice = env.token("alice")
    created = env.client.post(
        "/v1/ugen/keys", json={"name": "app"}, headers=bearer(alice)
    ).json()
    key = created["key"]
    assert created["redact_off"] == []

    def ask() -> dict:
        response = env.client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": MESSAGES},
            headers=bearer(key),
        )
        assert response.status_code == 200, response.text
        return response.json()

    reply = ask()
    assert "alex@example.com" not in _sent(env)
    redactions = reply["ugen"]["routing"]["redactions"]
    assert redactions and all("value" not in item for item in redactions)

    changed = env.client.put(
        f"/v1/ugen/keys/{created['id']}/redaction",
        json={"off": ["email"]},
        headers=bearer(alice),
    )
    assert changed.json()["off"] == ["email"]
    ask()
    assert "alex@example.com" in _sent(env)
    listed = env.client.get("/v1/ugen/keys", headers=bearer(alice)).json()["data"]
    assert listed[0]["redact_off"] == ["email"]
    missing = env.client.put(
        "/v1/ugen/keys/key_missing/redaction", json={"off": []}, headers=bearer(alice)
    )
    assert missing.status_code == 404
