"""Model prices for cost and savings reporting.

A built-in list (``model_prices.json``) covers the common models. Names are
compared loosely, so a model behind a proxy or cloud platform still finds its
price: ``anthropic/claude-haiku-5-5``, ``claude-haiku-5-5-20260101`` and
``us.anthropic.claude-haiku-5-5-v1:0`` all mean ``claude-haiku-5-5``. People add
or correct prices of their own on the Models page; those win over the list.

Prices only ever come from, in this order: the price typed for the model in its
own settings, the person's own list, the built-in list. A model found in none
of them is reported as unknown, never guessed.
"""

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_FILE = Path(__file__).with_name("model_prices.json")
CUSTOM_SETTING = "model_prices"

# Words that do not change which model is meant.
_NOISE = frozenset(
    {
        "preview",
        "latest",
        "exp",
        "experimental",
        "beta",
        "instruct",
        "high",
        "low",
        "medium",
        "minimal",
    }
)
# Hosting prefixes: "vertex_ai/", "bedrock/", "us.anthropic.", ...
_ROUTE_PREFIX = re.compile(
    r"^(?:(?:us|eu|apac|global|jp|au)\.)?"
    r"(?:anthropic|meta|mistral|amazon|cohere|deepseek|openai|google|xai)\.",
    re.IGNORECASE,
)
_SUFFIXES = (
    re.compile(r"@[\w-]+$"),  # vertex "@20250929"
    re.compile(r"-v\d+(?::\d+)?$"),  # bedrock "-v1:0"
    re.compile(r":[\w.-]+$"),  # ":latest", ":0"
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),  # "-2025-04-16"
    re.compile(r"-\d{2}-\d{2}$"),  # preview builds: "-06-17"
    re.compile(r"-\d{8}$"),  # "-20250514"
    re.compile(r"-\d{4}$"),  # "-0613"
)
_WORD_FUZZ = 0.8  # how alike two words must be to count as the same word


@dataclass(frozen=True)
class Price:
    """The price chosen for one model, and where it came from."""

    input: float = 0.0
    output: float = 0.0
    # local | configured | custom | catalog | unknown
    source: str = "unknown"
    # The list entry the name was matched to, when it came from a list.
    matched: str | None = None
    # exact | normalized | fuzzy, with how sure the match is (0 to 1).
    method: str | None = None
    confidence: float = 0.0
    status: str | None = None

    @property
    def known(self) -> bool:
        return self.source != "unknown"

    @property
    def label(self) -> str:
        """What is stored with a request: how its price was found."""
        if self.method == "fuzzy":
            return f"{self.source}-fuzzy"
        return self.source

    def view(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "output": self.output,
            "source": self.source,
            "matched": self.matched,
            "method": self.method,
            "confidence": self.confidence,
            "status": self.status,
        }


def normalize(name: str) -> str:
    """The name without host prefix, date, version tag or punctuation."""
    text = name.strip().lower().rsplit("/", 1)[-1]
    text = _ROUTE_PREFIX.sub("", text)
    changed = True
    while changed:
        changed = False
        for pattern in _SUFFIXES:
            stripped = pattern.sub("", text)
            if stripped != text and stripped:
                text, changed = stripped, True
    text = re.sub(r"[\s._]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def _parts(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(numbers in order, other words) of a normalized name."""
    tokens = [token for token in normalize(name).split("-") if token]
    numbers = tuple(token for token in tokens if token.isdigit())
    words = tuple(
        token for token in tokens if not token.isdigit() and token not in _NOISE
    )
    return numbers, words


def _word_match(a: tuple[str, ...], b: tuple[str, ...]) -> float | None:
    """Mean likeness when every word of ``a`` has a counterpart in ``b`` and
    the other way round, otherwise None."""
    if len(a) != len(b):
        return None
    unused = list(b)
    ratios = []
    for word in a:
        best = max(
            unused,
            key=lambda other: SequenceMatcher(None, word, other).ratio(),
            default=None,
        )
        if best is None:
            return None
        ratio = SequenceMatcher(None, word, best).ratio()
        if ratio < _WORD_FUZZ:
            return None
        unused.remove(best)
        ratios.append(ratio)
    return sum(ratios) / len(ratios)


def match_score(name: str, entry_id: str) -> tuple[str, float] | None:
    """How ``name`` matches a list entry: (method, confidence), or None.

    Version numbers must agree exactly, and so must the words that tell models
    apart (mini, nano, pro, flash, lite ...), so "gpt-5-mini" never matches
    "gpt-5" or "gpt-5-nano"."""
    if name.strip().lower() == entry_id.lower():
        return "exact", 1.0
    if normalize(name) == normalize(entry_id):
        return "normalized", 0.98
    numbers, words = _parts(name)
    entry_numbers, entry_words = _parts(entry_id)
    if numbers != entry_numbers:
        return None
    if sorted(words) == sorted(entry_words):
        return "normalized", 0.95  # same words in another order
    ratio = _word_match(words, entry_words)
    if ratio is None:
        return None
    return "fuzzy", round(0.9 * ratio, 2)


def _names(entry: dict[str, Any]) -> list[str]:
    return [entry["id"], *entry.get("aliases", [])]


def _best(
    name: str, entries: list[dict[str, Any]]
) -> tuple[dict[str, Any], str, float] | None:
    best: tuple[dict[str, Any], str, float] | None = None
    for entry in entries:
        for candidate in _names(entry):
            found = match_score(name, candidate)
            if found and (best is None or found[1] > best[2]):
                best = (entry, found[0], found[1])
    return best


@lru_cache
def catalog() -> list[dict[str, Any]]:
    """The built-in price list."""
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["models"]


def catalog_document() -> dict[str, Any]:
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def clean_custom(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the fields of a custom price entry, in a stable shape."""
    return [
        {
            "id": str(entry["id"]).strip(),
            "input": float(entry["input"]),
            "output": float(entry["output"]),
            **({"note": str(entry["note"]).strip()} if entry.get("note") else {}),
        }
        for entry in entries
    ]


class PriceBook:
    """Look prices up by model name: the person's own list first, then the
    built-in one."""

    def __init__(self, custom: list[dict[str, Any]] | None = None) -> None:
        self._custom = custom or []

    @classmethod
    def load(cls, store: Any | None) -> "PriceBook":
        saved = store.load_setting(CUSTOM_SETTING) if store is not None else None
        return cls(saved.get("custom", []) if isinstance(saved, dict) else [])

    @property
    def custom(self) -> list[dict[str, Any]]:
        return list(self._custom)

    def lookup(self, name: str) -> Price:
        """The price listed for ``name``, or an unknown price."""
        for source, entries in (("custom", self._custom), ("catalog", catalog())):
            found = _best(name, entries)
            if found:
                entry, method, confidence = found
                return Price(
                    input=float(entry["input"]),
                    output=float(entry["output"]),
                    source=source,
                    matched=entry["id"],
                    method=method,
                    confidence=confidence,
                    status=entry.get("status"),
                )
        return Price()

    def resolve(
        self,
        names: list[str],
        *,
        local: bool,
        configured_input: float = 0.0,
        configured_output: float = 0.0,
    ) -> Price:
        """The price of a model known by any of ``names``: a price typed for the
        model wins, local models otherwise cost nothing, then the lists."""
        if configured_input or configured_output:
            return Price(configured_input, configured_output, "configured")
        if local:
            return Price(source="local")
        best = Price()
        for name in names:
            if not name:
                continue
            found = self.lookup(name)
            if found.known and found.confidence > best.confidence:
                best = found
        return best
