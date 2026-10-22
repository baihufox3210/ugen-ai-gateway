"""Model connections, managed by administrators from the dashboard."""

import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ugen_ai_gateway.api.access import check_admin_access
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import (
    MODELS_SETTING,
    current_models,
    get_usage_store,
    models_changed,
)
from ugen_ai_gateway.providers.openai_client import create_openai_client
from ugen_ai_gateway.services.model_config import (
    CLOUD_PRESETS,
    LOCAL_PRESETS,
    CloudPreset,
    LocalPreset,
    StoredModels,
    public_view,
)
from ugen_ai_gateway.services.usage import UsageStore

router = APIRouter(prefix="/v1/ugen/models")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]
_PROBE_TIMEOUT = 30.0


def _check_url(value: str) -> str:
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("The base URL must start with http:// or https://")
    return value


class _Connection(BaseModel):
    base_url: str = Field(default="", max_length=500)
    # None keeps the saved key; "" removes it.
    api_key: str | None = Field(default=None, max_length=500)
    model: str = Field(default="", max_length=200)
    alias: str = Field(default="", max_length=100)

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _check_url(value)


class LocalUpdate(_Connection):
    preset: LocalPreset = "ollama"


class CloudUpdate(_Connection):
    input_cost_per_million: float = Field(default=0, ge=0, le=10_000)
    output_cost_per_million: float = Field(default=0, ge=0, le=10_000)


class ModelsUpdate(BaseModel):
    local: LocalUpdate
    cloud_enabled: bool = False
    cloud_active: CloudPreset = "openai"
    cloud: dict[CloudPreset, CloudUpdate] = Field(default_factory=dict)
    analysis_mode: Literal["hybrid", "llm"] = "hybrid"


class Probe(BaseModel):
    """A connection to try: ``target`` names whose saved key to use when
    ``api_key`` is not given ("local" or "cloud:<preset>")."""

    target: str = Field(pattern=r"^(local|cloud:(openai|gemini|anthropic|custom))$")
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    model: str = Field(default="", max_length=200)

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _check_url(value)


def _require_store(store: UsageStore | None) -> UsageStore:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Saving models needs a database; set USAGE_DB_PATH"
        )
    return store


def _strip(model: _Connection) -> dict[str, Any]:
    """The fields the request set, trimmed; the API key is handled separately."""
    data = model.model_dump(exclude={"api_key"}, exclude_unset=True)
    return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}


@router.get("")
async def get_models(store: UsageStoreDependency, request: Request) -> dict[str, Any]:
    await check_admin_access(request, store)
    models, source = current_models(store)
    return {
        "source": "routing_file" if get_settings().routing_config else source,
        "config": public_view(models),
        "presets": {"local": LOCAL_PRESETS, "cloud": CLOUD_PRESETS},
    }


@router.put("")
async def save_models(
    body: ModelsUpdate, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    await check_admin_access(request, store)
    store = _require_store(store)
    if get_settings().routing_config:
        raise HTTPException(
            status_code=409,
            detail="Models come from the ROUTING_CONFIG file; edit that file instead",
        )
    saved, _ = current_models(store)

    local = saved.local.model_copy(update=_strip(body.local))
    if body.local.api_key is not None:
        local.api_key = body.local.api_key.strip()
    local.base_url = local.base_url or LOCAL_PRESETS[local.preset]["base_url"]
    cloud = dict(saved.cloud)
    for name, update in body.cloud.items():
        merged = cloud[name].model_copy(update=_strip(update))
        if update.api_key is not None:
            merged.api_key = update.api_key.strip()
        # A preset left without a URL uses the provider's own.
        merged.base_url = merged.base_url or CLOUD_PRESETS[name]["base_url"]
        cloud[name] = merged

    models = StoredModels(
        local=local,
        cloud_enabled=body.cloud_enabled,
        cloud_active=body.cloud_active,
        cloud=cloud,
        analysis_mode=body.analysis_mode,
    )
    active = models.cloud[models.cloud_active]
    if models.cloud_enabled and not (active.api_key and active.model):
        label = CLOUD_PRESETS[models.cloud_active]["label"]
        raise HTTPException(
            status_code=422,
            detail=f"{label} needs an API key and a model before the cloud can be on",
        )
    if models.cloud_enabled and not active.base_url:
        raise HTTPException(status_code=422, detail="Enter the cloud base URL")

    await store.save_setting(MODELS_SETTING, models.model_dump())
    models_changed()
    return {"source": "database", "config": public_view(models)}


def _probe_client(probe: Probe, store: UsageStore | None):
    saved, _ = current_models(store)
    if probe.target == "local":
        saved_key, fallback = saved.local.api_key, "local"
    else:
        saved_key, fallback = saved.cloud[probe.target.split(":")[1]].api_key, ""  # type: ignore[index]
    key = probe.api_key.strip() if probe.api_key is not None else saved_key
    if not key and not fallback:
        raise HTTPException(status_code=422, detail="Enter the API key first")
    if not probe.base_url:
        raise HTTPException(status_code=422, detail="Enter the base URL first")
    return create_openai_client(
        api_key=key or fallback,
        base_url=probe.base_url,
        timeout=_PROBE_TIMEOUT,
        max_retries=0,
    )


def _short(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message if len(message) <= 300 else message[:300] + "…"


@router.post("/test")
async def test_connection(
    probe: Probe, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    """Send a tiny chat request with these settings, without saving them."""
    await check_admin_access(request, store)
    if not probe.model:
        raise HTTPException(status_code=422, detail="Choose a model first")
    client = _probe_client(probe, store)
    started = time.perf_counter()
    try:
        await client.chat.completions.create(
            model=probe.model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=16,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the administrator
        return {"ok": False, "error": _short(exc)}
    finally:
        await client.close()
    return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000)}


@router.post("/list")
async def list_models(
    probe: Probe, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    """The models the provider offers, to pick from."""
    await check_admin_access(request, store)
    client = _probe_client(probe, store)
    try:
        ids = sorted({model.id async for model in client.models.list()})
    except Exception as exc:  # noqa: BLE001 - reported to the administrator
        return {"models": [], "error": _short(exc)}
    finally:
        await client.close()
    # Gemini lists models as "models/<id>"; requests use the bare id.
    return {"models": [model_id.removeprefix("models/") for model_id in ids]}
