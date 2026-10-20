"""Request metadata and feedback stored in SQLite.

Only metadata is stored: routing, token counts, timings, cost, and which
personal-data types were found. Message text and personal-data values are
never written.
"""

import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    api_key_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    provider TEXT,
    provider_name TEXT,
    model TEXT,
    rule TEXT,
    task_type TEXT,
    complexity TEXT,
    analysis_source TEXT,
    personal_data_types TEXT NOT NULL DEFAULT '[]',
    redacted_values INTEGER NOT NULL DEFAULT 0,
    fallback_from TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    analysis_ms REAL NOT NULL DEFAULT 0,
    generation_ms REAL NOT NULL DEFAULT 0,
    total_ms REAL NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    baseline_cost_usd REAL NOT NULL DEFAULT 0,
    injection_signals TEXT NOT NULL DEFAULT '[]',
    output_redactions INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS requests_created_at ON requests (created_at);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    request_id TEXT,
    event TEXT NOT NULL,
    detail TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    revoked_at REAL,
    last_used_at REAL,
    allow_cloud INTEGER NOT NULL DEFAULT 1,
    monthly_budget_usd REAL,
    rate_limit_per_minute INTEGER
);
CREATE TABLE IF NOT EXISTS feedback (
    request_id TEXT PRIMARY KEY REFERENCES requests (id),
    rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    created_at REAL NOT NULL
);
"""


@dataclass
class UsageRecord:
    id: str
    status: str = "ok"
    error: str | None = None
    api_key_id: str | None = None
    provider: str | None = None
    provider_name: str | None = None
    model: str | None = None
    rule: str | None = None
    task_type: str | None = None
    complexity: str | None = None
    analysis_source: str | None = None
    personal_data_types: list[str] = field(default_factory=list)
    redacted_values: int = 0
    fallback_from: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    analysis_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0
    cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0
    injection_signals: list[str] = field(default_factory=list)
    output_redactions: int = 0
    created_at: float = field(default_factory=time.time)


def token_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_per_million: float,
    output_per_million: float,
) -> float:
    return (
        prompt_tokens * input_per_million + completion_tokens * output_per_million
    ) / 1_000_000


_ADDED_COLUMNS = {
    "injection_signals": "TEXT NOT NULL DEFAULT '[]'",
    "output_redactions": "INTEGER NOT NULL DEFAULT 0",
}
_GENESIS_HASH = "0" * 64


def _audit_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    canonical = json.dumps(
        entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(f"{prev_hash}|{canonical}".encode()).hexdigest()


class UsageStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # Several gateway processes may share one file: wait for locks instead
        # of failing, and use WAL so readers do not block the writer.
        self._connection = sqlite3.connect(
            self._path, check_same_thread=False, timeout=30
        )
        self._connection.row_factory = sqlite3.Row
        if self._path != ":memory:":
            self._connection.execute("PRAGMA journal_mode=WAL")
        self._lock = Lock()
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        existing = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(requests)")
        }
        for name, definition in _ADDED_COLUMNS.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE requests ADD COLUMN {name} {definition}"
                )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    # Writes and reads run in a worker thread so the event loop never waits on disk.

    async def record(self, record: UsageRecord) -> None:
        await asyncio.to_thread(self._record, record)

    # ``api_key_id`` scopes reads and feedback to one tenant; None means all.

    async def set_feedback(
        self, request_id: str, rating: int, *, api_key_id: str | None = None
    ) -> bool:
        return await asyncio.to_thread(
            self._set_feedback, request_id, rating, api_key_id
        )

    async def stats(
        self, *, days: int, tz_offset_minutes: int = 0, api_key_id: str | None = None
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats, days, tz_offset_minutes, api_key_id)

    async def recent(
        self, *, limit: int, before: float | None = None, api_key_id: str | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent, limit, before, api_key_id)

    def _record(self, record: UsageRecord) -> None:
        row = asdict(record)
        row["personal_data_types"] = json.dumps(record.personal_data_types)
        row["injection_signals"] = json.dumps(record.injection_signals)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        with self._lock, self._connection:
            self._connection.execute(
                f"INSERT OR REPLACE INTO requests ({columns}) VALUES ({placeholders})",
                row,
            )

    def _set_feedback(
        self, request_id: str, rating: int, api_key_id: str | None
    ) -> bool:
        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM requests WHERE id = ? AND (? IS NULL OR api_key_id = ?)",
                (request_id, api_key_id, api_key_id),
            ).fetchone()
            if not exists:
                return False
            self._connection.execute(
                "INSERT INTO feedback (request_id, rating, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT (request_id) DO UPDATE SET rating = excluded.rating, "
                "created_at = excluded.created_at",
                (request_id, rating, time.time()),
            )
            return True

    def _stats(
        self, days: int, tz_offset_minutes: int, api_key_id: str | None
    ) -> dict[str, Any]:
        since = time.time() - days * 86400
        offset = tz_offset_minutes * 60
        scope = "(? IS NULL OR api_key_id = ?)"
        key = (api_key_id, api_key_id)
        with self._lock:
            totals = self._connection.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COALESCE(SUM(status = 'ok'), 0) AS completed,
                    COALESCE(SUM(status = 'error'), 0) AS failed,
                    COALESCE(SUM(status = 'cancelled'), 0) AS cancelled,
                    COALESCE(SUM(status = 'ok' AND provider = 'local'), 0) AS local,
                    COALESCE(SUM(status = 'ok' AND provider = 'cloud'), 0) AS cloud,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(generation_ms), 0) AS generation_ms,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd,
                    COALESCE(SUM(baseline_cost_usd), 0) AS baseline_cost_usd,
                    COALESCE(SUM(personal_data_types != '[]'), 0) AS with_personal_data,
                    COALESCE(SUM(redacted_values), 0) AS redacted_values,
                    COALESCE(SUM(fallback_from IS NOT NULL), 0) AS fallbacks,
                    COALESCE(SUM(analysis_source = 'rules'), 0) AS analysis_rules,
                    COALESCE(SUM(analysis_source = 'cache'), 0) AS analysis_cache,
                    COALESCE(SUM(injection_signals != '[]'), 0) AS injection_flagged,
                    COALESCE(SUM(status = 'blocked'), 0) AS blocked,
                    COALESCE(SUM(output_redactions), 0) AS output_redactions,
                    COALESCE(AVG(CASE WHEN status = 'ok' THEN total_ms END), 0) AS avg_total_ms
                FROM requests WHERE created_at >= ? AND {scope}
                """.replace("{scope}", scope),
                (since, *key),
            ).fetchone()
            daily = self._connection.execute(
                """
                SELECT date(created_at + ?, 'unixepoch') AS day, COUNT(*) AS requests,
                       COALESCE(SUM(provider = 'cloud'), 0) AS cloud
                FROM requests WHERE created_at >= ? AND {scope}
                GROUP BY day ORDER BY day
                """.replace("{scope}", scope),
                (offset, since, *key),
            ).fetchall()
            rules = self._connection.execute(
                """
                SELECT COALESCE(rule, 'default') AS rule, COUNT(*) AS requests
                FROM requests WHERE created_at >= ? AND status = 'ok' AND {scope}
                GROUP BY rule ORDER BY requests DESC
                """.replace("{scope}", scope),
                (since, *key),
            ).fetchall()
            feedback = self._connection.execute(
                """
                SELECT COALESCE(SUM(rating = 1), 0) AS up, COALESCE(SUM(rating = -1), 0) AS down
                FROM feedback JOIN requests ON requests.id = feedback.request_id
                WHERE requests.created_at >= ? AND {scope}
                """.replace(
                    "{scope}", scope.replace("api_key_id", "requests.api_key_id")
                ),
                (since, *key),
            ).fetchone()

        result = dict(totals)
        seconds = result.pop("generation_ms") / 1000
        result["tokens_per_second"] = (
            round(result["completion_tokens"] / seconds, 1) if seconds > 0 else None
        )
        result["savings_usd"] = max(
            result["baseline_cost_usd"] - result["cost_usd"], 0.0
        )
        result["days"] = days
        result["daily"] = [dict(row) for row in daily]
        result["rules"] = [dict(row) for row in rules]
        result["feedback"] = dict(feedback)
        return result

    def _recent(
        self, limit: int, before: float | None, api_key_id: str | None
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT requests.*, feedback.rating FROM requests "
            "LEFT JOIN feedback ON feedback.request_id = requests.id "
            "WHERE (? IS NULL OR requests.api_key_id = ?)"
        )
        parameters: tuple[Any, ...] = (api_key_id, api_key_id)
        if before is not None:
            query += " AND requests.created_at < ?"
            parameters = (*parameters, before)
        query += " ORDER BY requests.created_at DESC LIMIT ?"
        with self._lock:
            rows = self._connection.execute(query, (*parameters, limit)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["personal_data_types"] = json.loads(item["personal_data_types"])
            item["injection_signals"] = json.loads(item["injection_signals"])
            items.append(item)
        return items

    # Audit log: append-only, each entry hashes the previous entry's hash, so a
    # changed, removed or reordered row breaks verification from that point on.

    async def append_audit(
        self, event: str, *, request_id: str | None, detail: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._append_audit, event, request_id, detail)

    async def audit_entries(
        self, *, since: float | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._audit_entries, since, limit)

    async def verify_audit(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._verify_audit)

    def _append_audit(
        self, event: str, request_id: str | None, detail: dict[str, Any]
    ) -> None:
        # BEGIN IMMEDIATE takes SQLite's write lock before reading the last
        # hash, so processes sharing the file cannot both chain onto it.
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            last = self._connection.execute(
                "SELECT hash FROM audit ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = last["hash"] if last else _GENESIS_HASH
            entry = {
                "created_at": time.time(),
                "request_id": request_id,
                "event": event,
                "detail": detail,
            }
            self._connection.execute(
                "INSERT INTO audit (created_at, request_id, event, detail, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry["created_at"],
                    request_id,
                    event,
                    json.dumps(detail, sort_keys=True, ensure_ascii=False),
                    prev_hash,
                    _audit_hash(prev_hash, entry),
                ),
            )

    def _audit_entries(self, since: float | None, limit: int) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit"
        parameters: tuple[Any, ...] = ()
        if since is not None:
            query += " WHERE created_at >= ?"
            parameters = (since,)
        query += " ORDER BY seq LIMIT ?"
        with self._lock:
            rows = self._connection.execute(query, (*parameters, limit)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def _verify_audit(self) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audit ORDER BY seq"
            ).fetchall()
        prev_hash = _GENESIS_HASH
        for row in rows:
            entry = {
                "created_at": row["created_at"],
                "request_id": row["request_id"],
                "event": row["event"],
                "detail": json.loads(row["detail"]),
            }
            if row["prev_hash"] != prev_hash or row["hash"] != _audit_hash(
                prev_hash, entry
            ):
                return {
                    "ok": False,
                    "entries": len(rows),
                    "first_invalid_seq": row["seq"],
                }
            prev_hash = row["hash"]
        return {"ok": True, "entries": len(rows), "first_invalid_seq": None}

    # API keys. Only a SHA-256 hash of each secret is stored; the secret itself
    # is returned once, when the key is created.

    async def create_key(
        self,
        *,
        name: str,
        allow_cloud: bool = True,
        monthly_budget_usd: float | None = None,
        rate_limit_per_minute: int | None = None,
    ) -> tuple[dict[str, Any], str]:
        return await asyncio.to_thread(
            self._create_key,
            name,
            allow_cloud,
            monthly_budget_usd,
            rate_limit_per_minute,
        )

    async def list_keys(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_keys)

    async def revoke_key(self, key_id: str) -> bool:
        return await asyncio.to_thread(self._revoke_key, key_id)

    async def find_key(self, secret: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._find_key, secret)

    async def has_keys(self) -> bool:
        """Whether any API key was ever created, including revoked ones."""
        return await asyncio.to_thread(self._has_keys)

    async def month_spend(self, key_id: str) -> float:
        return await asyncio.to_thread(self._month_spend, key_id)

    def _create_key(
        self,
        name: str,
        allow_cloud: bool,
        monthly_budget_usd: float | None,
        rate_limit_per_minute: int | None,
    ) -> tuple[dict[str, Any], str]:
        secret = "ugk_" + secrets.token_urlsafe(32)
        row = {
            "id": "key_" + secrets.token_hex(6),
            "name": name,
            "prefix": secret[:10],
            "key_hash": _key_hash(secret),
            "created_at": time.time(),
            "allow_cloud": int(allow_cloud),
            "monthly_budget_usd": monthly_budget_usd,
            "rate_limit_per_minute": rate_limit_per_minute,
        }
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        with self._lock, self._connection:
            self._connection.execute(
                f"INSERT INTO api_keys ({columns}) VALUES ({placeholders})", row
            )
        return self._key_view(row | {"revoked_at": None, "last_used_at": None}), secret

    def _list_keys(self) -> list[dict[str, Any]]:
        month_start = _month_start()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT api_keys.*, COALESCE(SUM(requests.cost_usd), 0) AS month_cost_usd,
                       COUNT(requests.id) AS month_requests
                FROM api_keys LEFT JOIN requests
                  ON requests.api_key_id = api_keys.id AND requests.created_at >= ?
                GROUP BY api_keys.id ORDER BY api_keys.created_at
                """,
                (month_start,),
            ).fetchall()
        return [self._key_view(dict(row)) for row in rows]

    def _revoke_key(self, key_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (time.time(), key_id),
            )
            return cursor.rowcount > 0

    def _find_key(self, secret: str) -> dict[str, Any] | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                (_key_hash(secret),),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
        return self._key_view(dict(row))

    def _has_keys(self) -> bool:
        with self._lock:
            return (
                self._connection.execute("SELECT 1 FROM api_keys LIMIT 1").fetchone()
                is not None
            )

    def _month_spend(self, key_id: str) -> float:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS spend FROM requests "
                "WHERE api_key_id = ? AND created_at >= ?",
                (key_id, _month_start()),
            ).fetchone()
        return float(row["spend"])

    @staticmethod
    def _key_view(row: dict[str, Any]) -> dict[str, Any]:
        row.pop("key_hash", None)
        row["allow_cloud"] = bool(row["allow_cloud"])
        return row


def _key_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _month_start() -> float:
    """Start of the current calendar month, UTC."""
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC).timestamp()
