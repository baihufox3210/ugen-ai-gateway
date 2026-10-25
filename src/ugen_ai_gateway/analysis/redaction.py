"""Reversible redaction: replace personal data with numbered placeholders and
restore the original values in model output, without the values ever leaving
this process."""

import re
from collections.abc import Iterable
from typing import Any

from ugen_ai_gateway.analysis.pii import _INSTALLED, find_personal_data, value_pattern

PLACEHOLDER_PATTERN = re.compile(r"\[([A-Z][A-Z_]*)_(\d+)\]")
# A trailing fragment that could still grow into a placeholder, e.g. "[EMA" or "[EMAIL_1".
_PARTIAL_PLACEHOLDER = re.compile(r"\[[A-Z_]*\d*\Z")
_MAX_PLACEHOLDER_LENGTH = 40
ADDITIONAL_VALUE_LABEL = "PERSONAL_DATA"

PLACEHOLDER_INSTRUCTION = (
    "Some values in this conversation were replaced with placeholders such as "
    "[EMAIL_1] to protect personal data. Keep every placeholder exactly as written "
    "when you refer to it, and never guess or invent the value behind it."
)


class RedactionVault:
    """Per-request mapping between placeholders and the values they replace.

    The same value always gets the same placeholder, so the model can tell that
    two mentions refer to one thing. The mapping lives only in memory.
    """

    def __init__(
        self,
        additional_values: Iterable[str] = (),
        *,
        custom_finder: Any = _INSTALLED,
        skip_types: Iterable[str] = (),
    ) -> None:
        # Types of personal data left as written, chosen by the account or API
        # key; "other" stands for values only the analysis model pointed out.
        self._skip_types = frozenset(skip_types)
        # Custom terms to use instead of the installed ones (previews only).
        self._custom_finder = custom_finder
        self._additional_values = sorted(
            {value for value in additional_values if len(value) >= 2},
            key=len,
            reverse=True,
        )
        self._placeholder_by_value: dict[str, str] = {}
        self._value_by_placeholder: dict[str, str] = {}
        self._type_by_placeholder: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._reserved: set[str] = set()

    def __len__(self) -> int:
        return len(self._value_by_placeholder)

    def values(self) -> list[str]:
        return list(self._value_by_placeholder.values())

    def entries(self) -> list[dict[str, str]]:
        """What was replaced: placeholder, type of data and the original value."""
        return [
            {
                "placeholder": placeholder,
                "type": self._type_by_placeholder[placeholder],
                "value": value,
            }
            for placeholder, value in self._value_by_placeholder.items()
        ]

    def reserve(self, texts: Iterable[str]) -> None:
        """Never issue placeholders that already appear in the input."""
        for text in texts:
            self._reserved.update(
                match.group() for match in PLACEHOLDER_PATTERN.finditer(text)
            )

    def redact(self, text: str, *, trusted: bool = False) -> str:
        """Replace personal data in ``text``. ``trusted`` text, such as the
        administrators' own system prompt, is only checked for structured data
        (numbers, keys, custom terms): name recognition mistakes ordinary words
        in a long prompt for people, and the model's own findings concern the
        user's text."""
        redacted = text
        for match in reversed(
            find_personal_data(
                text, custom_finder=self._custom_finder, entities=not trusted
            )
        ):
            if match.type in self._skip_types:
                continue
            placeholder = self._placeholder(match.value, match.label, match.type)
            redacted = redacted[: match.start] + placeholder + redacted[match.end :]

        for value in self._additional_values:
            if trusted or "other" in self._skip_types or value not in redacted:
                continue
            redacted = value_pattern(value).sub(
                lambda _, value=value: self._placeholder(
                    value, ADDITIONAL_VALUE_LABEL, "other"
                ),
                redacted,
            )
        return redacted

    def restore(self, text: str) -> str:
        if not self._value_by_placeholder:
            return text
        return PLACEHOLDER_PATTERN.sub(
            lambda match: self._value_by_placeholder.get(match.group(), match.group()),
            text,
        )

    def _placeholder(self, value: str, label: str, kind: str) -> str:
        existing = self._placeholder_by_value.get(value)
        if existing is not None:
            return existing
        while True:
            self._counters[label] = self._counters.get(label, 0) + 1
            placeholder = f"[{label}_{self._counters[label]}]"
            if placeholder not in self._reserved:
                break
        self._placeholder_by_value[value] = placeholder
        self._value_by_placeholder[placeholder] = value
        self._type_by_placeholder[placeholder] = kind
        return placeholder


class StreamRestorer:
    """Restore placeholders in streamed text, even when one is split across chunks."""

    def __init__(self, vault: RedactionVault) -> None:
        self._vault = vault
        self._pending = ""

    def feed(self, text: str) -> str:
        buffer = self._pending + text
        start = buffer.rfind("[")
        tail = buffer[start:] if start != -1 else ""
        if (
            tail
            and len(tail) <= _MAX_PLACEHOLDER_LENGTH
            and _PARTIAL_PLACEHOLDER.match(tail)
        ):
            self._pending = tail
            buffer = buffer[:start]
        else:
            self._pending = ""
        return self._vault.restore(buffer)

    def flush(self) -> str:
        rest, self._pending = self._pending, ""
        return self._vault.restore(rest)
