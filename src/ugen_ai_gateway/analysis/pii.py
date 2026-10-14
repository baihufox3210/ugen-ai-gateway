import re
from collections.abc import Iterable

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


def detect_personal_data(text: str) -> list[str]:
    """Detect common personal-data patterns in text."""
    detected: list[str] = []

    if EMAIL_PATTERN.search(text):
        detected.append("email")

    if TAIWAN_MOBILE_PATTERN.search(text):
        detected.append("taiwan_mobile")

    if any(
        _is_valid_taiwan_id(match.group()) for match in TAIWAN_ID_PATTERN.finditer(text)
    ):
        detected.append("taiwan_id")

    if any(
        _is_valid_credit_card(match.group())
        for match in CREDIT_CARD_PATTERN.finditer(text)
    ):
        detected.append("credit_card")

    return detected


def redact_personal_data(text: str, *, additional_values: Iterable[str] = ()) -> str:
    """Replace supported personal-data patterns with descriptive markers."""
    redacted = EMAIL_PATTERN.sub("[EMAIL]", text)
    redacted = TAIWAN_MOBILE_PATTERN.sub("[TAIWAN_MOBILE]", redacted)
    redacted = TAIWAN_ID_PATTERN.sub(
        lambda match: (
            "[TAIWAN_ID]" if _is_valid_taiwan_id(match.group()) else match.group()
        ),
        redacted,
    )
    redacted = CREDIT_CARD_PATTERN.sub(
        lambda match: (
            "[CREDIT_CARD]" if _is_valid_credit_card(match.group()) else match.group()
        ),
        redacted,
    )

    for value in sorted(set(additional_values), key=len, reverse=True):
        if len(value) < 2 or value not in redacted:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
        redacted = pattern.sub("[PERSONAL_DATA]", redacted)

    return redacted
