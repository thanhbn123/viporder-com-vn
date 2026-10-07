"""Duplicate rules.

Two things matter here:

* A phone that already has a REGISTERED lead is a duplicate → 409, and the
  response must not reveal the existing customer code. Otherwise knowing any
  phone number would be enough to harvest customer codes.
* A phone whose earlier lead is still PENDING/FAILED is *not* a duplicate. That
  customer has no account yet; refusing them would strand them forever.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Lead, LeadType, RegistrationStatus, utcnow
from app.providers.base import ProviderStatus, RegistrationResult
from tests.conftest import Harness, payload


class FlakyProvider:
    """Fails the first attempt, succeeds afterwards."""

    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def register(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return RegistrationResult(
                status=ProviderStatus.UNAVAILABLE,
                message="first attempt fails",
                http_status=503,
                retryable=True,
            )
        return RegistrationResult(
            status=ProviderStatus.SUCCESS,
            external_customer_id="ext-2",
            external_customer_code="TT00077",
            message="ok",
            http_status=201,
        )


def test_second_registration_for_a_registered_phone_is_409(harness: Harness) -> None:
    first = harness.post_registration(payload(phone="0912345678"))
    assert first.status_code == 201

    second = harness.post_registration(payload(phone="+84 912 345 678"))
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "DUPLICATE_PHONE"


def test_duplicate_response_does_not_leak_the_customer_code(harness: Harness) -> None:
    first = harness.post_registration().json()
    existing_code = first["external_customer_code"]
    existing_lead_id = first["lead_id"]

    second = harness.post_registration()
    assert second.status_code == 409

    text = second.text
    assert existing_code not in text
    assert existing_lead_id not in text
    assert "external_customer_code" not in text
    # And no new lead row was created for the duplicate attempt.
    assert len(harness.lead_rows()) == 1


def test_duplicate_detection_ignores_phone_formatting(harness: Harness) -> None:
    assert harness.post_registration(payload(phone="0912 345 678")).status_code == 201
    for variant in ("+84912345678", "84912345678", "0912.345.678", "(091) 234-5678"):
        response = harness.post_registration(payload(phone=variant))
        assert response.status_code == 409, variant


def test_pending_lead_does_not_block_a_new_attempt(make_harness) -> None:
    harness = make_harness(provider=FlakyProvider())

    first = harness.post_registration(payload(phone="0912345678"))
    assert first.status_code == 202
    assert first.json()["registration_status"] == "PENDING"

    second = harness.post_registration(payload(phone="0912345678"))
    assert second.status_code == 201, second.text
    assert second.json()["registration_status"] == "REGISTERED"

    rows = harness.lead_rows()
    assert len(rows) == 2, "a new attempt must be a new row, not a merge"
    assert rows[0].lead_id == first.json()["lead_id"]
    assert rows[0].registration_status is RegistrationStatus.PENDING
    assert rows[1].lead_id == second.json()["lead_id"]
    assert rows[1].registration_status is RegistrationStatus.REGISTERED


def test_different_customers_are_never_merged(harness: Harness) -> None:
    harness.post_registration(payload(phone="0912000011", full_name="Khách Một"))
    harness.post_registration(
        payload(phone="0912000012", full_name="Khách Hai", email="hai@example.com")
    )

    rows = harness.lead_rows()
    assert len(rows) == 2
    by_name = {row.full_name: row for row in rows}
    assert by_name["Khách Một"].phone == "+84912000011"
    assert by_name["Khách Hai"].phone == "+84912000012"
    assert by_name["Khách Một"].email == "a@example.com"
    assert by_name["Khách Hai"].email == "hai@example.com"


def test_provider_reported_duplicate_is_409_and_the_lead_is_kept(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="duplicate")
    response = harness.post_registration()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_PHONE"
    body = response.json()
    assert "external_customer_code" not in str(body)

    rows = harness.lead_rows()
    assert len(rows) == 1, "the lead must be retained, never dropped"
    assert rows[0].registration_status is RegistrationStatus.FAILED
    assert rows[0].last_error_code == "DUPLICATE_PHONE"


def test_partial_unique_index_blocks_two_registered_rows(harness: Harness) -> None:
    """The rule is enforced by the database, not only by application code."""
    harness.post_registration(payload(phone="0912345678"))

    now = utcnow()
    with harness.database.session() as session:
        session.add(
            Lead(
                lead_id="forced-duplicate",
                lead_type=LeadType.REGISTER_LEAD,
                registration_status=RegistrationStatus.REGISTERED,
                full_name="Kẻ trùng",
                phone="+84912345678",
                phone_display="0912 345 678",
                tracking_token="forced-token",
                attempt_count=1,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_partial_unique_index_allows_many_failed_rows(harness: Harness) -> None:
    """PENDING/FAILED rows are outside the index on purpose."""
    now = utcnow()
    with harness.database.session() as session:
        for index in range(3):
            session.add(
                Lead(
                    lead_id=f"failed-{index}",
                    lead_type=LeadType.REGISTER_LEAD,
                    registration_status=RegistrationStatus.FAILED,
                    full_name="Khách thử lại",
                    phone="+84912345678",
                    phone_display="0912 345 678",
                    tracking_token=f"token-{index}",
                    attempt_count=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()

    with harness.database.session() as session:
        assert session.query(Lead).count() == 3
