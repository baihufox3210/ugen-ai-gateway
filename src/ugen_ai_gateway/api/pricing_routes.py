"""Model prices for cost and savings: the built-in list, the administrators'
own additions and corrections, and a checker for how a model name is matched."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ugen_ai_gateway.api.access import audit_admin, check_admin_access
from ugen_ai_gateway.dependencies import (
    get_gateway_service,
    get_usage_store,
    models_changed,
)
from ugen_ai_gateway.services.pricing import (
    CUSTOM_SETTING,
    PriceBook,
    catalog_document,
    clean_custom,
    normalize,
)
from ugen_ai_gateway.services.usage import UsageStore

router = APIRouter(prefix="/v1/ugen/pricing")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]
MAX_CUSTOM = 200


class CustomPrice(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    input: float = Field(ge=0, le=100_000)
    output: float = Field(ge=0, le=100_000)
    note: str = Field(default="", max_length=200)


class CustomPrices(BaseModel):
    custom: list[CustomPrice] = Field(default_factory=list, max_length=MAX_CUSTOM)


@router.get("")
async def get_pricing(store: UsageStoreDependency, request: Request) -> dict[str, Any]:
    """The built-in list, the custom prices, and the cloud models used lately
    with how each one's price was found."""
    await check_admin_access(request, store)
    document = catalog_document()
    return {
        "updated": document["updated"],
        "note": document["note"],
        "unit": document["unit"],
        "catalog": document["models"],
        "custom": PriceBook.load(store).custom,
        "used": await store.price_usage() if store is not None else [],
    }


@router.put("")
async def save_pricing(
    body: CustomPrices, store: UsageStoreDependency, request: Request
) -> dict[str, Any]:
    await check_admin_access(request, store)
    if store is None:
        raise HTTPException(
            status_code=503, detail="Saving needs a database; set USAGE_DB_PATH"
        )
    names = [normalize(price.id) for price in body.custom]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise HTTPException(
            status_code=422,
            detail="Each custom price needs its own, different model name",
        )
    custom = clean_custom([price.model_dump() for price in body.custom])
    await store.save_setting(CUSTOM_SETTING, {"custom": custom})
    models_changed()
    get_gateway_service()  # use the new prices from the next request
    await audit_admin(
        request, store, "pricing_updated", models=[c["id"] for c in custom]
    )
    return {"custom": custom}


@router.get("/match")
async def match_model(
    store: UsageStoreDependency,
    request: Request,
    name: Annotated[str, Query(min_length=1, max_length=200)],
) -> dict[str, Any]:
    """Which price a model name would get, and how it was matched."""
    await check_admin_access(request, store)
    price = PriceBook.load(store).lookup(name)
    return {"name": name, "normalized": normalize(name), **price.view()}
