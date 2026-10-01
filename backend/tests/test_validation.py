"""Request validation and the 422/400/413 envelopes."""

from __future__ import annotations

import pytest

from tests.conftest import DEFAULT_PASSWORD, Harness, payload


def _fields(response) -> dict:
    return response.json()["error"].get("fields", {})


@pytest.mark.parametrize("missing", ["full_name", "phone", "password", "consent"])
def test_missing_required_field_is_422(harness: Harness, missing: str) -> None:
    response = harness.post_registration(payload(**{missing: ...}))
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert missing in _fields(response)


@pytest.mark.parametrize("name", ["A", "", "  ", "x" * 121])
def test_full_name_length_is_enforced(harness: Harness, name: str) -> None:
    response = harness.post_registration(payload(full_name=name))
    assert response.status_code == 422
    assert "full_name" in _fields(response)


def test_full_name_is_trimmed() -> None:
    from app.schemas import RegistrationCreate

    model = RegistrationCreate.model_validate(payload(full_name="  Nguyễn Văn A  "))
    assert model.full_name == "Nguyễn Văn A"


@pytest.mark.parametrize("password", ["short", "1234567", ""])
def test_short_password_is_422(harness: Harness, password: str) -> None:
    response = harness.post_registration(payload(password=password))
    assert response.status_code == 422
    assert "password" in _fields(response)


def test_password_field_never_echoes_the_value(harness: Harness) -> None:
    """A rejected password must not come back in the error body."""
    leaked = "abc"
    response = harness.post_registration(payload(password=leaked))
    assert response.status_code == 422
    assert leaked not in response.text


@pytest.mark.parametrize("email", ["not-an-email", "a@", "@example.com", "a b@example.com"])
def test_bad_email_is_422(harness: Harness, email: str) -> None:
    response = harness.post_registration(payload(email=email))
    assert response.status_code == 422
    assert "email" in _fields(response)


def test_empty_email_is_treated_as_absent(harness: Harness) -> None:
    response = harness.post_registration(payload(email=""))
    assert response.status_code == 201
    lead = harness.lead_row(response.json()["lead_id"])
    assert lead.email is None


@pytest.mark.parametrize("interest", ["Transport", "shipping", "custom"])
def test_bad_service_interest_is_422(harness: Harness, interest: str) -> None:
    response = harness.post_registration(payload(service_interest=interest))
    assert response.status_code == 422
    assert "service_interest" in _fields(response)


def test_blank_service_interest_is_treated_as_absent(harness: Harness) -> None:
    response = harness.post_registration(payload(service_interest=""))
    assert response.status_code == 201
    assert harness.lead_row(response.json()["lead_id"]).service_interest is None


@pytest.mark.parametrize(
    ("interest", "phone"),
    [
        ("transport", "0912000011"),
        ("official_import", "0912000012"),
        ("customs", "0912000013"),
        ("order", "0912000014"),
    ],
)
def test_every_allowed_service_interest_is_accepted(
    harness: Harness, interest: str, phone: str
) -> None:
    response = harness.post_registration(payload(service_interest=interest, phone=phone))
    assert response.status_code == 201, response.text


def test_consent_false_is_rejected(harness: Harness) -> None:
    response = harness.post_registration(payload(consent=False))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "consent" in _fields(response)


def test_consent_missing_is_rejected(harness: Harness) -> None:
    response = harness.post_registration(payload(consent=...))
    assert response.status_code == 422
    assert "consent" in _fields(response)


def test_no_lead_is_written_for_invalid_input(harness: Harness) -> None:
    harness.post_registration(payload(consent=False))
    assert harness.lead_rows() == []


@pytest.mark.parametrize("field", ["utm_source", "landing_page", "referrer"])
def test_attribution_field_over_300_chars_is_422(harness: Harness, field: str) -> None:
    response = harness.post_registration(
        payload(attribution={**payload()["attribution"], field: "x" * 301})
    )
    assert response.status_code == 422
    assert any(field in key for key in _fields(response))


@pytest.mark.parametrize(
    "value", ["/relative/path", "javascript:alert(1)", "viporder.com.vn", "ftp://x"]
)
def test_landing_page_must_be_absolute_http_url(harness: Harness, value: str) -> None:
    response = harness.post_registration(
        payload(attribution={**payload()["attribution"], "landing_page": value})
    )
    assert response.status_code == 422


def test_attribution_is_optional(harness: Harness) -> None:
    response = harness.post_registration(payload(attribution=...))
    assert response.status_code == 201
    lead = harness.lead_row(response.json()["lead_id"])
    assert lead.source is None
    assert lead.landing_page is None


def test_empty_attribution_subfields_become_null(harness: Harness) -> None:
    response = harness.post_registration(
        payload(attribution={"utm_source": "", "utm_medium": "  "})
    )
    assert response.status_code == 201
    lead = harness.lead_row(response.json()["lead_id"])
    assert lead.source is None and lead.medium is None


def test_oversized_body_is_413(harness: Harness) -> None:
    huge = payload(full_name="x" * 100_000)
    response = harness.post_registration(huge)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_malformed_json_is_400(harness: Harness) -> None:
    response = harness.client.post(
        "/api/v1/registrations",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_password_is_a_secret_type() -> None:
    """The model must not render the password anywhere, ever."""
    from app.schemas import RegistrationCreate

    model = RegistrationCreate.model_validate(payload(password=DEFAULT_PASSWORD))
    assert DEFAULT_PASSWORD not in repr(model)
    assert DEFAULT_PASSWORD not in str(model)
    assert DEFAULT_PASSWORD not in str(model.model_dump())
    assert model.password.get_secret_value() == DEFAULT_PASSWORD
