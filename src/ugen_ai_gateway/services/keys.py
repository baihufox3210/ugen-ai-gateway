from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Principal:
    """Who is calling, and what their API key allows.

    ``api_key_id`` is None for the master GATEWAY_API_KEY and for open
    (keyless) development mode; those callers have no extra restrictions.
    """

    api_key_id: str | None
    allow_cloud: bool = True
    monthly_budget_usd: float | None = None
    rate_limit_per_minute: int | None = None

    @classmethod
    def from_key(cls, key: dict[str, Any]) -> "Principal":
        return cls(
            api_key_id=key["id"],
            allow_cloud=bool(key["allow_cloud"]),
            monthly_budget_usd=key["monthly_budget_usd"],
            rate_limit_per_minute=key["rate_limit_per_minute"],
        )
