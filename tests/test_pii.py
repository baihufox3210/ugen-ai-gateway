import pytest

from ugen_ai_gateway.analysis.pii import detect_personal_data


@pytest.mark.parametrize(
    "email",
    [
        "alex@example.com",
        "alex.smith+work@example.co.uk",
        "alex_smith-2@sub.example.com",
    ],
)
def test_detects_email_addresses(email: str) -> None:
    assert detect_personal_data(f"Contact me at {email}.") == ["email"]


@pytest.mark.parametrize(
    "phone_number",
    [
        "0912345678",
        "0912-345-678",
        "0912 345 678",
        "+886912345678",
        "+886 912 345 678",
        "886-912-345-678",
    ],
)
def test_detects_taiwan_mobile_numbers(phone_number: str) -> None:
    assert detect_personal_data(f"Call me: {phone_number}") == ["taiwan_mobile"]


def test_detects_multiple_categories_in_stable_order() -> None:
    text = "Email alex@example.com or call 0912-345-678."

    assert detect_personal_data(text) == ["email", "taiwan_mobile"]


def test_reports_each_category_once_when_multiple_values_are_present() -> None:
    text = "alex@example.com, sam@example.org, 0912345678, +886 912 345 678"

    assert detect_personal_data(text) == ["email", "taiwan_mobile"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No contact information is included.",
        "alex@example",
        "alex@example..com",
        "091234567",
        "09123456789",
        "88691234567",
        "Call 1234567890 instead.",
    ],
)
def test_returns_empty_for_text_without_supported_personal_data(text: str) -> None:
    assert detect_personal_data(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "alex@example.com-",
        "x0912345678",
        "0912345678x",
    ],
)
def test_does_not_match_identifiers_embedded_in_word_like_text(text: str) -> None:
    assert detect_personal_data(text) == []
