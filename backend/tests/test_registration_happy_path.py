"""Happy path: 201, REGISTERED, lead row with the external code."""

from __future__ import annotations

import re

from tests.conftest import Harness, payload

LOGIN_URL = "https://khachhang.viporder.com.vn"


def test_registration_returns_201_and_registered(harness: Harness) -> None:
    response = harness.post_registration()

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["registration_status"] == "REGISTERED"
    assert body["login_url"] == LOGIN_URL
    assert body["external_customer_id"]
    assert re.fullmatch(r"TT\d{5}", body["external_customer_code"])
    assert body["message"]


def test_lead_row_exists_with_external_code(harness: Harness) -> None:
    body = harness.post_registration().json()

    lead = harness.lead_row(body["lead_id"])
    assert lead is not None
    assert lead.registration_status.value == "REGISTERED"
    assert lead.external_customer_code == body["external_customer_code"]
    assert lead.external_customer_id == body["external_customer_id"]
    assert lead.lead_type.value == "REGISTER_LEAD"


def test_lead_row_stores_normalised_identity(harness: Harness) -> None:
    body = harness.post_registration(
        payload(phone="+84 912 345 678", full_name="  Trần Thị B  ")
    ).json()

    lead = harness.lead_row(body["lead_id"])
    assert lead.phone == "+84912345678"
    assert lead.phone_display == "0912 345 678"
    assert lead.full_name == "Trần Thị B"
    assert lead.email == "a@example.com"
    assert lead.province == "Bắc Ninh"
    assert lead.service_interest == "transport"


def test_utm_attribution_is_mapped_to_columns(harness: Harness) -> None:
    body = harness.post_registration().json()
    lead = harness.lead_row(body["lead_id"])

    assert lead.source == "facebook"
    assert lead.medium == "cpc"
    assert lead.campaign == "g01"
    assert lead.content == "ad1"
    assert lead.term == "nhaphang"
    assert lead.landing_page == "https://viporder.com.vn/?utm_source=facebook"
    assert lead.referrer == "https://facebook.com/"


def test_timestamps_are_set_and_utc(harness: Harness) -> None:
    from app.models import as_utc

    body = harness.post_registration().json()
    lead = harness.lead_row(body["lead_id"])
    assert lead.created_at is not None
    assert lead.updated_at is not None
    # SQLite hands back a naive datetime; `as_utc` is what the API uses, so this
    # asserts the value the client actually sees is labelled UTC.
    assert as_utc(lead.created_at).tzinfo is not None


def test_tracking_token_is_present_and_opaque(harness: Harness) -> None:
    lead = harness.lead_row(harness.post_registration().json()["lead_id"])
    assert lead.tracking_token
    assert len(lead.tracking_token) >= 32
    assert " " not in lead.tracking_token


def test_attempt_count_starts_at_one(harness: Harness) -> None:
    lead = harness.lead_row(harness.post_registration().json()["lead_id"])
    assert lead.attempt_count == 1


def test_registration_does_not_require_attribution(harness: Harness) -> None:
    response = harness.post_registration(payload(attribution=...))
    assert response.status_code == 201
    lead = harness.lead_row(response.json()["lead_id"])
    assert (lead.source, lead.medium, lead.campaign) == (None, None, None)


def test_two_different_customers_both_register(harness: Harness) -> None:
    first = harness.post_registration(payload(phone="0912000011"))
    second = harness.post_registration(payload(phone="0912000012", email="b@example.com"))
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["lead_id"] != second.json()["lead_id"]
    assert len(harness.lead_rows()) == 2


def test_tracking_token_can_read_the_status(harness: Harness) -> None:
    lead = harness.lead_row(harness.post_registration().json()["lead_id"])

    response = harness.client.get(
        f"/api/v1/registrations/{lead.lead_id}",
        headers={"X-Tracking-Token": lead.tracking_token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["lead_id"] == lead.lead_id
    assert body["registration_status"] == "REGISTERED"
    assert body["external_customer_code"] == lead.external_customer_code
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")


def test_status_endpoint_never_returns_phone_or_email(harness: Harness) -> None:
    lead = harness.lead_row(harness.post_registration().json()["lead_id"])

    response = harness.client.get(
        f"/api/v1/registrations/{lead.lead_id}",
        headers={"X-Tracking-Token": lead.tracking_token},
    )
    text = response.text
    assert "0912345678" not in text
    assert "+84912345678" not in text
    assert "a@example.com" not in text
    assert set(response.json()) == {
        "lead_id",
        "registration_status",
        "external_customer_code",
        "created_at",
        "updated_at",
    }


def test_status_without_token_is_404(harness: Harness) -> None:
    lead = harness.lead_row(harness.post_registration().json()["lead_id"])
    response = harness.client.get(f"/api/v1/registrations/{lead.lead_id}")
    assert response.status_code == 404


def test_status_with_wrong_token_is_404(harness: Harness) -> None:
    lead = harness.lead_row(harness.post_registration().json()["lead_id"])
    response = harness.client.get(
        f"/api/v1/registrations/{lead.lead_id}",
        headers={"X-Tracking-Token": "not-the-token"},
    )
    assert response.status_code == 404


def test_status_for_unknown_lead_is_404(harness: Harness) -> None:
    response = harness.client.get(
        "/api/v1/registrations/00000000-0000-0000-0000-000000000000",
        headers={"X-Tracking-Token": "anything"},
    )
    assert response.status_code == 404
