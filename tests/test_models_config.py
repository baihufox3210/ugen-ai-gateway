from types import SimpleNamespace
from typing import Any

from tests.test_accounts import AccountEnv, bearer
from ugen_ai_gateway import dependencies
from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.services.model_config import (
    StoredModels,
    from_settings,
    to_routing,
)

LOCAL = {
    "preset": "ollama",
    "base_url": "http://localhost:11434/v1",
    "model": "qwen3:8b",
}


def body(**overrides: Any) -> dict[str, Any]:
    return {
        "local": LOCAL,
        "cloud_enabled": False,
        "cloud_active": "openai",
        "cloud": {},
        **overrides,
    }


class ModelsEnv(AccountEnv):
    def __init__(self, monkeypatch: Any, **settings: Any) -> None:
        super().__init__(monkeypatch, **settings)
        for module in ("model_routes", "dependencies"):
            target = (
                f"ugen_ai_gateway.api.{module}.get_settings"
                if module == "model_routes"
                else "ugen_ai_gateway.dependencies.get_settings"
            )
            monkeypatch.setattr(target, lambda: self.settings)
        monkeypatch.setattr(dependencies, "get_usage_store", lambda: self.store)
        self.client.app.dependency_overrides.pop(dependencies.get_gateway_service)  # type: ignore[attr-defined]
        dependencies.models_changed()
        self.admin = self.token("alice")

    def put(self, payload: dict[str, Any], token: str | None = None) -> Any:
        return self.client.put(
            "/v1/ugen/models", json=payload, headers=bearer(token or self.admin)
        )

    def get(self, token: str | None = None) -> Any:
        return self.client.get("/v1/ugen/models", headers=bearer(token or self.admin))


def test_only_administrators_see_or_change_models(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    member = env.token("bob")
    assert env.get(member).status_code == 403
    assert env.put(body(), member).status_code == 403
    assert env.client.get("/v1/ugen/models").status_code == 403


def test_saved_models_drive_the_gateway_without_a_restart(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    assert env.get().json()["source"] == "env"

    cloud = {
        "openai": {
            "api_key": "sk-test-1234567890",
            "model": "gpt-big",
            "alias": "Smart",
        }
    }
    saved = env.put(
        body(cloud_enabled=True, cloud=cloud, local={**LOCAL, "alias": "House"})
    )
    assert saved.status_code == 200, saved.text

    config = env.client.get("/v1/ugen/config").json()
    assert config["local_model"] == "House"
    assert config["cloud_model"] == "Smart"
    assert config["cloud_enabled"] is True
    service = dependencies.get_gateway_service()
    assert (
        service.engine.default("cloud").provider.base_url == "https://api.openai.com/v1"
    )


def test_api_keys_are_never_returned_and_kept_when_omitted(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    cloud = {"gemini": {"api_key": "AIza-secret-abcd", "model": "gemini-x"}}
    env.put(body(cloud_enabled=True, cloud_active="gemini", cloud=cloud))

    view = env.get().json()
    assert "AIza-secret-abcd" not in str(view)
    assert view["config"]["cloud"]["gemini"]["has_api_key"] is True
    assert view["config"]["cloud"]["gemini"]["api_key_hint"] == "…abcd"

    # Saving again without a key keeps it; "" removes it.
    env.put(
        body(
            cloud_enabled=True,
            cloud_active="gemini",
            cloud={"gemini": {"model": "gemini-y"}},
        )
    )
    stored = env.store.load_setting("models")
    assert stored["cloud"]["gemini"]["api_key"] == "AIza-secret-abcd"
    assert stored["cloud"]["gemini"]["model"] == "gemini-y"
    env.put(body(cloud={"gemini": {"api_key": "", "model": "gemini-y"}}))
    assert env.store.load_setting("models")["cloud"]["gemini"]["api_key"] == ""


def test_switching_the_active_cloud_keeps_every_providers_key(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    cloud = {
        "openai": {"api_key": "sk-openai-key-1", "model": "gpt-x"},
        "anthropic": {"api_key": "sk-ant-key-2222", "model": "claude-x"},
    }
    env.put(body(cloud_enabled=True, cloud_active="openai", cloud=cloud))
    env.put(body(cloud_enabled=True, cloud_active="anthropic"))

    route = dependencies.get_gateway_service().engine.default("cloud")
    assert route.model.id == "claude-x"
    assert route.provider.base_url == "https://api.anthropic.com/v1/"
    assert (
        env.store.load_setting("models")["cloud"]["openai"]["api_key"]
        == "sk-openai-key-1"
    )


def test_cloud_cannot_be_turned_on_without_a_key_and_model(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    response = env.put(body(cloud_enabled=True, cloud_active="gemini"))
    assert response.status_code == 422
    assert "Gemini" in response.json()["detail"]
    assert (
        env.put(body(local={**LOCAL, "base_url": "localhost:11434"})).status_code == 422
    )


def test_routing_file_takes_precedence(monkeypatch, tmp_path) -> None:
    env = ModelsEnv(monkeypatch, routing_config=str(tmp_path / "routing.yaml"))
    assert env.get().json()["source"] == "routing_file"
    assert env.put(body()).status_code == 409


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=self.list)

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("401 invalid api key")
        return SimpleNamespace(choices=[])

    async def _items(self) -> Any:
        for model_id in ("models/gemini-b", "models/gemini-a"):
            yield SimpleNamespace(id=model_id)

    def list(self) -> Any:
        return self._items()

    async def close(self) -> None:
        pass


def test_test_and_list_use_the_saved_key_unless_one_is_given(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    env.put(body(cloud={"gemini": {"api_key": "saved-key-9999", "model": "gemini-a"}}))
    used: list[str] = []
    fake = FakeClient()

    def factory(**kwargs: Any) -> FakeClient:
        used.append(kwargs["api_key"])
        return fake

    monkeypatch.setattr(
        "ugen_ai_gateway.api.model_routes.create_openai_client", factory
    )
    probe = {
        "target": "cloud:gemini",
        "base_url": "https://g.example/v1",
        "model": "gemini-a",
    }
    tested = env.client.post(
        "/v1/ugen/models/test", json=probe, headers=bearer(env.admin)
    )
    assert tested.json()["ok"] is True
    listed = env.client.post(
        "/v1/ugen/models/list",
        json={**probe, "api_key": "typed-key"},
        headers=bearer(env.admin),
    )
    assert listed.json()["models"] == ["gemini-a", "gemini-b"]
    assert used == ["saved-key-9999", "typed-key"]

    monkeypatch.setattr(
        "ugen_ai_gateway.api.model_routes.create_openai_client",
        lambda **_: FakeClient(fail=True),
    )
    failed = env.client.post(
        "/v1/ugen/models/test", json=probe, headers=bearer(env.admin)
    )
    assert failed.json() == {"ok": False, "error": "401 invalid api key"}


def test_env_settings_seed_the_first_configuration() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        local_base_url="http://localhost:11434/v1",
        local_model="qwen",
        cloud_api_key="AIza-key",
        cloud_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        cloud_model="gemini-flash",
    )
    seeded = from_settings(settings)
    assert seeded.local.preset == "ollama" and seeded.local.model == "qwen"
    assert seeded.cloud_enabled and seeded.cloud_active == "gemini"
    assert seeded.cloud["gemini"].model == "gemini-flash"
    routing = to_routing(seeded)
    assert [p.locality for p in routing.providers] == ["local", "cloud"]


def test_no_cloud_when_disabled() -> None:
    stored = StoredModels.model_validate(
        {"local": LOCAL, "cloud": {"openai": {"api_key": "k", "model": "m"}}}
    )
    assert [p.name for p in to_routing(stored).providers] == ["local"]


def test_unconfigured_gateway_points_to_the_models_page(monkeypatch) -> None:
    env = ModelsEnv(monkeypatch)
    response = env.chat(env.admin)
    assert response.status_code == 503
    assert "Models page" in response.json()["detail"]
