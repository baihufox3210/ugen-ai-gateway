"""Model connections managed from the dashboard and stored in the database.

One local model, plus saved credentials for each cloud preset with one of
them active. This replaces the LOCAL_* / CLOUD_* settings, which only seed
the first configuration.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from ugen_ai_gateway.config import Settings
from ugen_ai_gateway.routing.config import (
    Condition,
    Defaults,
    ModelConfig,
    ProviderConfig,
    RoutingConfig,
    Rule,
    Target,
)

LocalPreset = Literal["ollama", "lmstudio", "custom"]
CloudPreset = Literal["openai", "gemini", "anthropic", "custom"]

# Every preset speaks the OpenAI Chat Completions API at this base URL.
LOCAL_PRESETS: dict[str, dict[str, str]] = {
    "ollama": {"label": "Ollama", "base_url": "http://localhost:11434/v1"},
    "lmstudio": {"label": "LM Studio", "base_url": "http://localhost:1234/v1"},
    "custom": {"label": "OpenAI-compatible", "base_url": ""},
}
CLOUD_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "ChatGPT",
        "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "anthropic": {
        "label": "Claude",
        "base_url": "https://api.anthropic.com/v1/",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "custom": {"label": "OpenAI-compatible", "base_url": "", "key_url": ""},
}


class LocalModel(BaseModel):
    preset: LocalPreset = "ollama"
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    alias: str = Field(default="", max_length=100)


class CloudModel(BaseModel):
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    alias: str = Field(default="", max_length=100)
    # USD per million tokens, for the cost and savings figures.
    input_cost_per_million: float = Field(default=0, ge=0)
    output_cost_per_million: float = Field(default=0, ge=0)


def _default_clouds() -> dict[str, CloudModel]:
    return {
        name: CloudModel(base_url=preset["base_url"])
        for name, preset in CLOUD_PRESETS.items()
    }


class StoredModels(BaseModel):
    local: LocalModel = Field(default_factory=LocalModel)
    cloud_enabled: bool = False
    cloud_active: CloudPreset = "openai"
    cloud: dict[CloudPreset, CloudModel] = Field(default_factory=_default_clouds)
    # hybrid: rules when a request stays local anyway; llm: the local model
    # judges every request.
    analysis_mode: Literal["hybrid", "llm"] = "hybrid"

    def active_cloud(self) -> CloudModel | None:
        cloud = self.cloud.get(self.cloud_active)
        if self.cloud_enabled and cloud and cloud.api_key and cloud.model:
            return cloud
        return None


def from_settings(settings: Settings) -> StoredModels:
    """The configuration the LOCAL_* / CLOUD_* settings describe."""
    local_preset = next(
        (
            name
            for name, preset in LOCAL_PRESETS.items()
            if preset["base_url"] and preset["base_url"] == settings.local_base_url
        ),
        "custom" if settings.local_base_url else "ollama",
    )
    stored = StoredModels(
        local=LocalModel(
            preset=local_preset,  # type: ignore[arg-type]
            base_url=settings.local_base_url or LOCAL_PRESETS["ollama"]["base_url"],
            api_key="" if settings.local_api_key == "local" else settings.local_api_key,
            model=settings.local_model,
            alias=settings.local_model_alias,
        ),
        analysis_mode="llm" if settings.analysis_mode == "llm" else "hybrid",
    )
    if settings.cloud_api_key or settings.cloud_model:
        cloud_base = settings.cloud_base_url.rstrip("/")
        active = next(
            (
                name
                for name, preset in CLOUD_PRESETS.items()
                if preset["base_url"]
                and preset["base_url"].rstrip("/")
                == (cloud_base or CLOUD_PRESETS["openai"]["base_url"])
            ),
            "custom",
        )
        stored.cloud_active = active  # type: ignore[assignment]
        stored.cloud[active] = CloudModel(  # type: ignore[index]
            base_url=settings.cloud_base_url or CLOUD_PRESETS[active]["base_url"],
            api_key=settings.cloud_api_key,
            model=settings.cloud_model,
            alias=settings.cloud_model_alias,
        )
        stored.cloud_enabled = bool(settings.cloud_api_key and settings.cloud_model)
    return stored


def to_routing(stored: StoredModels) -> RoutingConfig:
    """Routing equivalent to the stored models: moderate and complex requests
    go to the active cloud model when there is one."""
    local = stored.local
    providers = [
        ProviderConfig(
            name="local",
            locality="local",
            base_url=local.base_url or None,
            api_key=local.api_key or "local",
            models=[ModelConfig(id=local.model, alias=local.alias or None)],
        )
    ]
    rules: list[Rule] = []
    cloud_target: Target | None = None
    if cloud := stored.active_cloud():
        providers.append(
            ProviderConfig(
                name="cloud",
                locality="cloud",
                base_url=cloud.base_url or None,
                api_key=cloud.api_key,
                models=[
                    ModelConfig(
                        id=cloud.model,
                        alias=cloud.alias or None,
                        input_cost_per_million=cloud.input_cost_per_million,
                        output_cost_per_million=cloud.output_cost_per_million,
                    )
                ],
            )
        )
        cloud_target = Target(provider="cloud")
        rules.append(
            Rule(
                name="moderate-or-complex-to-cloud",
                when=Condition(complexity=["moderate", "complex"]),
                route=cloud_target,
            )
        )
    return RoutingConfig(
        providers=providers,
        defaults=Defaults(local=Target(provider="local"), cloud=cloud_target),
        analysis=Target(provider="local"),
        rules=rules,
    )


def _key_hint(key: str) -> str:
    return f"…{key[-4:]}" if len(key) > 8 else ("set" if key else "")


def public_view(stored: StoredModels) -> dict[str, Any]:
    """The configuration without secrets: keys become a short hint."""

    def masked(model: BaseModel) -> dict[str, Any]:
        data = model.model_dump()
        key = data.pop("api_key")
        return {**data, "has_api_key": bool(key), "api_key_hint": _key_hint(key)}

    return {
        "local": masked(stored.local),
        "cloud_enabled": stored.cloud_enabled,
        "cloud_active": stored.cloud_active,
        "cloud": {name: masked(model) for name, model in stored.cloud.items()},
        "analysis_mode": stored.analysis_mode,
    }
