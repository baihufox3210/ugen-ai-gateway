"""Conversations saved for dashboard accounts, so history follows a person
across browsers and devices.

Each conversation is stored as one compressed blob, encrypted with the
gateway's secret key (``secret.key`` or ``SECRET_KEY``); titles, messages and
any personal data in them are never written in plain text. Only the account
that saved a conversation can read it, administrators included.
"""

import json
import logging
import time
import zlib
from typing import Annotated, Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ugen_ai_gateway.api.access import SESSION_PREFIX, presented_api_key
from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.dependencies import get_secret_box, get_usage_store
from ugen_ai_gateway.services.usage import StorageFull, UsageStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/ugen/conversations")

UsageStoreDependency = Annotated[UsageStore | None, Depends(get_usage_store)]

# Uncompressed JSON size of one conversation.
MAX_CONVERSATION_BYTES = 5_000_000
CONVERSATION_ID = r"^[A-Za-z0-9_-]{1,64}$"


class SavedConversation(BaseModel):
    """The dashboard's conversation object; fields beyond ``id`` are kept as
    sent, so the dashboard can add fields without a server change."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=CONVERSATION_ID)


class SyncRequest(BaseModel):
    save: list[SavedConversation] = Field(default_factory=list, max_length=500)
    delete: list[Annotated[str, Field(pattern=CONVERSATION_ID)]] = Field(
        default_factory=list, max_length=5000
    )


async def _account(
    request: Request, store: UsageStore | None
) -> tuple[UsageStore, str]:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Saving conversations needs a database"
        )
    token = presented_api_key(request)
    user = await store.find_session(token) if token.startswith(SESSION_PREFIX) else None
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to sync conversations")
    return store, user["id"]


@router.get("")
async def list_conversations(
    store: UsageStoreDependency, request: Request, since: float | None = None
) -> dict[str, Any]:
    """Conversations changed after ``since`` (a ``server_time`` from an
    earlier response; omit it for all), and the ids of all of them."""
    store, user_id = await _account(request, store)
    # Taken before reading, so a save racing this request shows up next time.
    server_time = time.time()
    rows, ids = await store.list_conversations(user_id, since)
    box = get_secret_box()
    conversations, unreadable = [], 0
    for row in rows:
        try:
            conversations.append(json.loads(zlib.decompress(box.decrypt(row["data"]))))
        except (InvalidToken, zlib.error, ValueError):
            unreadable += 1
    if unreadable:
        logger.warning(
            "%d saved conversation(s) cannot be decrypted; was secret.key or "
            "SECRET_KEY changed?",
            unreadable,
        )
    return {
        "conversations": conversations,
        "ids": ids,
        "server_time": server_time,
        "unreadable": unreadable,
    }


@router.post("/sync")
async def sync_conversations(
    body: SyncRequest, store: UsageStoreDependency, request: Request
) -> dict[str, float]:
    """Save and delete conversations in one step. A saved conversation
    replaces the stored one with the same id."""
    store, user_id = await _account(request, store)
    box = get_secret_box()
    items = []
    for conversation in body.save:
        raw = json.dumps(
            conversation.model_dump(), ensure_ascii=False, separators=(",", ":")
        ).encode()
        if len(raw) > MAX_CONVERSATION_BYTES:
            raise HTTPException(
                status_code=413,
                detail="This conversation is too long to save; start a new one",
            )
        data = box.encrypt(zlib.compress(raw, 6))
        items.append((conversation.id, data, len(data)))
    limit = int(get_settings().conversation_storage_mb * 1_000_000)
    try:
        server_time = await store.sync_conversations(
            user_id, items, body.delete, max_total_bytes=limit
        )
    except StorageFull as exc:
        raise HTTPException(
            status_code=507,
            detail="Your saved conversations are full; delete some old ones",
        ) from exc
    return {"server_time": server_time}
