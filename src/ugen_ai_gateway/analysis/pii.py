import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w+-]+(?:\.[\w+-]+)*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?![\w-])"
)

TAIWAN_MOBILE_PATTERN = re.compile(
    r"(?<!\w)(?:09\d{2}|(?:\+?886[- ]?)9\d{2})[- ]?\d{3}[- ]?\d{3}(?!\w)"
)
TAIWAN_ID_PATTERN = re.compile(r"(?<!\w)[A-Z][12]\d{8}(?!\w)", re.IGNORECASE)
CREDIT_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

_TAIWAN_ID_CODES = {
    "A": 10,
    "B": 11,
    "C": 12,
    "D": 13,
    "E": 14,
    "F": 15,
    "G": 16,
    "H": 17,
    "I": 34,
    "J": 18,
    "K": 19,
    "L": 20,
    "M": 21,
    "N": 22,
    "O": 35,
    "P": 23,
    "Q": 24,
    "R": 25,
    "S": 26,
    "T": 27,
    "U": 28,
    "V": 29,
    "W": 32,
    "X": 30,
    "Y": 31,
    "Z": 33,
}


def _is_valid_taiwan_id(value: str) -> bool:
    code = _TAIWAN_ID_CODES.get(value[0].upper())
    if code is None:
        return False

    digits = [int(digit) for digit in value[1:]]
    checksum = code // 10 + (code % 10) * 9
    checksum += sum(digit * weight for digit, weight in zip(digits, range(8, -1, -1)))
    return checksum % 10 == 0


def _is_valid_credit_card(value: str) -> bool:
    digits = [int(digit) for digit in value if digit.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False

    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


@dataclass(frozen=True)
class Detector:
    """A personal-data type: how to find it and how to label it."""

    type: str
    label: str
    pattern: re.Pattern[str]
    validate: Callable[[str], bool] | None = None

    def matches(self, text: str) -> Iterator[re.Match[str]]:
        for match in self.pattern.finditer(text):
            if self.validate is None or self.validate(match.group()):
                yield match


@dataclass(frozen=True)
class PersonalDataMatch:
    type: str
    label: str
    start: int
    end: int
    value: str


DETECTORS: tuple[Detector, ...] = (
    Detector("email", "EMAIL", EMAIL_PATTERN),
    Detector("taiwan_mobile", "TAIWAN_MOBILE", TAIWAN_MOBILE_PATTERN),
    Detector("taiwan_id", "TAIWAN_ID", TAIWAN_ID_PATTERN, _is_valid_taiwan_id),
    Detector("credit_card", "CREDIT_CARD", CREDIT_CARD_PATTERN, _is_valid_credit_card),
)
SUPPORTED_TYPES = frozenset(detector.type for detector in DETECTORS)


def find_personal_data(text: str) -> list[PersonalDataMatch]:
    """Return non-overlapping personal-data matches in text order.

    Detectors earlier in ``DETECTORS`` win when two matches overlap.
    """
    found: list[PersonalDataMatch] = []
    for detector in DETECTORS:
        for match in detector.matches(text):
            if any(
                match.start() < other.end and other.start < match.end()
                for other in found
            ):
                continue
            found.append(
                PersonalDataMatch(
                    detector.type,
                    detector.label,
                    match.start(),
                    match.end(),
                    match.group(),
                )
            )
    return sorted(found, key=lambda item: item.start)


def detect_personal_data(text: str) -> list[str]:
    """Detect common personal-data patterns in text."""
    return [
        detector.type
        for detector in DETECTORS
        if next(detector.matches(text), None) is not None
    ]


def redact_personal_data(text: str, *, additional_values: Iterable[str] = ()) -> str:
    """Replace supported personal-data patterns with descriptive markers."""
    redacted = text
    for match in reversed(find_personal_data(text)):
        redacted = redacted[: match.start] + f"[{match.label}]" + redacted[match.end :]

    for value in sorted(set(additional_values), key=len, reverse=True):
        if len(value) < 2 or value not in redacted:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
        redacted = pattern.sub("[PERSONAL_DATA]", redacted)

    return redacted
