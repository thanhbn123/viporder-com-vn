"""Consent must be *evidenced*, not merely enforced.

`consent: true` being required by the request schema is enforcement. It answers
"may we create this account?". It does not answer "can we show that this
customer agreed, and to which wording?" — which is a personal-data question
asked months later, by someone who was not there.

These tests pin the evidence: every lead carries the time of consent and the
version of the wording, and the version is a real constant rather than a
free-text field the application invents per request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import CONSENT_VERSION
from app.models import as_utc
from tests.conftest import Harness, payload


def test_registration_records_when_and_what_the_customer_consented_to(
    harness: Harness,
) -> None:
    before = datetime.now(UTC)
    response = harness.post_registration()
    after = datetime.now(UTC)

    assert response.status_code == 201, response.text
    lead = harness.lead_row(response.json()["lead_id"])

    assert lead.consent_given_at is not None
    assert lead.consent_version == CONSENT_VERSION

    given_at = as_utc(lead.consent_given_at)
    assert given_at.tzinfo is not None, "consent timestamps must be timezone-aware"
    assert before - timedelta(seconds=5) <= given_at <= after + timedelta(seconds=5)


def test_consent_evidence_is_recorded_even_when_the_provider_is_down(
    make_harness,
) -> None:
    """Consent was given to *us*; it does not depend on the provider."""
    harness = make_harness(mock_provider_behaviour="unavailable")
    response = harness.post_registration()
    assert response.status_code == 202

    lead = harness.lead_row(response.json()["lead_id"])
    assert lead.consent_given_at is not None
    assert lead.consent_version == CONSENT_VERSION


def test_no_lead_exists_without_consent(harness: Harness) -> None:
    response = harness.post_registration(payload(consent=False))
    assert response.status_code == 422
    assert harness.lead_rows() == [], "no consent means no row at all"


def test_consent_version_is_a_short_constant() -> None:
    assert isinstance(CONSENT_VERSION, str)
    assert 0 < len(CONSENT_VERSION) <= 32, "must fit the column"
    assert CONSENT_VERSION.strip() == CONSENT_VERSION


def test_consent_columns_exist_in_the_model() -> None:
    from app.models import Lead

    columns = Lead.__table__.columns
    assert "consent_given_at" in columns
    assert "consent_version" in columns
    assert columns["consent_given_at"].type.timezone is True


def test_every_registration_lead_has_consent_evidence(harness: Harness) -> None:
    """Not a spot check: every row written by the service."""
    for index in range(3):
        assert harness.post_registration(payload(phone=f"091200001{index}")).status_code == 201

    rows = harness.lead_rows()
    assert len(rows) == 3
    for row in rows:
        assert row.consent_given_at is not None, row.lead_id
        assert row.consent_version == CONSENT_VERSION, row.lead_id
