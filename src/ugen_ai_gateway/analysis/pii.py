import re

EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w+-]+(?:\.[\w+-]+)*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?![\w-])"
)

TAIWAN_MOBILE_PATTERN = re.compile(
    r"(?<!\w)(?:09\d{2}|(?:\+?886[- ]?)9\d{2})[- ]?\d{3}[- ]?\d{3}(?!\w)"
)


def detect_personal_data(text: str) -> list[str]:
    """Detect common personal-data patterns in text."""
    detected: list[str] = []

    if EMAIL_PATTERN.search(text):
        detected.append("email")

    if TAIWAN_MOBILE_PATTERN.search(text):
        detected.append("taiwan_mobile")

    return detected


def redact_personal_data(text: str) -> str:
    """Replace supported personal-data patterns with descriptive markers."""
    redacted = EMAIL_PATTERN.sub("[EMAIL]", text)
    return TAIWAN_MOBILE_PATTERN.sub("[TAIWAN_MOBILE]", redacted)
