"""Idempotency.

A retried POST (double-click, flaky network, a client retry loop) must produce
one lead row and the same response body — not a second customer.

An ``Idempotency-Key`` alone is not enough to decide "this is the same request".
The key is scoped to a browser form session, and the client keeps it across
failed attempts, so "same key, edited phone number" is reachable. Every replay
therefore also compares a fingerprint of the canonical request body; a mismatch
is refused with ``409 IDEMPOTENCY_KEY_REUSED`` and never returns the earlier
customer's data.
"""

from __future__ import annotations

import pytest

from tests.conftest import Harness, payload


def test_same_idempotency_key_twice_creates_one_lead(harness: Harness) -> None:
    headers = {"Idempotency-Key": "9f1c0a5e-abc"}

    first = harness.post_registration(headers=headers)
    second = harness.post_registration(headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert len(harness.lead_rows()) == 1


def test_different_keys_create_different_leads(harness: Harness) -> None:
    first = harness.post_registration(
        payload(phone="0912000011"), headers={"Idempotency-Key": "key-a"}
    )
    second = harness.post_registration(
        payload(phone="0912000012"), headers={"Idempotency-Key": "key-b"}
    )

    assert first.json()["lead_id"] != second.json()["lead_id"]
    assert len(harness.lead_rows()) == 2


def test_idempotent_replay_does_not_call_the_provider_twice(make_harness) -> None:
    from app.providers.base import ProviderStatus, RegistrationResult

    class CountingProvider:
        name = "counting"

        def __init__(self) -> None:
            self.calls = 0

        def register(self, request):  # type: ignore[no-untyped-def]
            self.calls += 1
            return RegistrationResult(
                status=ProviderStatus.SUCCESS,
                external_customer_id="ext-1",
                external_customer_code="TT00001",
                message="ok",
                http_status=201,
            )

    provider = CountingProvider()
    harness = make_harness(provider=provider)
    headers = {"Idempotency-Key": "replay-me"}

    harness.post_registration(headers=headers)
    harness.post_registration(headers=headers)

    assert provider.calls == 1


def test_idempotent_replay_of_a_pending_lead_returns_202_and_the_same_token(
    make_harness,
) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")
    headers = {"Idempotency-Key": "pending-key"}

    first = harness.post_registration(headers=headers)
    second = harness.post_registration(headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["tracking_token"]
    assert len(harness.lead_rows()) == 1


def test_idempotency_key_is_stored(harness: Harness) -> None:
    body = harness.post_registration(headers={"Idempotency-Key": "stored-key"}).json()
    assert harness.lead_row(body["lead_id"]).idempotency_key == "stored-key"


def test_no_idempotency_key_creates_a_second_attempt_for_a_failed_lead(
    make_harness,
) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")
    harness.post_registration(payload(phone="0912000011"))
    harness.post_registration(payload(phone="0912000011"))
    assert len(harness.lead_rows()) == 2


def test_overlong_idempotency_key_is_400(harness: Harness) -> None:
    response = harness.post_registration(headers={"Idempotency-Key": "k" * 200})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_blank_idempotency_key_is_ignored(harness: Harness) -> None:
    response = harness.post_registration(headers={"Idempotency-Key": "   "})
    assert response.status_code == 201
    assert harness.lead_row(response.json()["lead_id"]).idempotency_key is None


# --- the key is bound to the body, not just to itself ----------------------


def test_same_key_with_a_different_phone_is_refused(harness: Harness) -> None:
    """The cross-customer leak.

    The front end keeps one Idempotency-Key for the whole form session and only
    clears it after a completed registration, so "submit, get an error, correct
    the phone number, submit again" reuses the key with a different body. A
    blind replay would answer customer B with customer A's lead.
    """
    headers = {"Idempotency-Key": "idem-1"}

    first = harness.post_registration(payload(phone="0912000011"), headers=headers)
    assert first.status_code == 201
    first_body = first.json()

    second = harness.post_registration(payload(phone="0912000012"), headers=headers)

    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # Neither customer's identifying data may appear in the refusal.
    text = second.text
    assert first_body["external_customer_code"] not in text
    assert first_body["lead_id"] not in text
    assert "external_customer_code" not in text
    assert "+84912000012" not in text

    # And the second customer's attempt created nothing.
    assert len(harness.lead_rows()) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"full_name": "Nguyễn Văn B"},
        {"phone": "0912000012"},
        {"email": "different@example.com"},
        {"service_interest": "customs"},
        {"province": "Hà Nội"},
    ],
)
def test_same_key_with_any_different_identity_field_is_refused(
    harness: Harness, override: dict
) -> None:
    headers = {"Idempotency-Key": "idem-fields"}
    assert harness.post_registration(payload(), headers=headers).status_code == 201
    changed = harness.post_registration(payload(**override), headers=headers)
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_same_key_with_different_attribution_is_refused(harness: Harness) -> None:
    """Different campaign means a different submission, not a retry."""
    headers = {"Idempotency-Key": "idem-utm"}
    assert harness.post_registration(payload(), headers=headers).status_code == 201

    changed = harness.post_registration(
        payload(
            attribution={**payload()["attribution"], "utm_campaign": "g99"},
        ),
        headers=headers,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_same_key_and_same_body_still_replays_identically(harness: Harness) -> None:
    headers = {"Idempotency-Key": "idem-same"}
    first = harness.post_registration(payload(), headers=headers)
    second = harness.post_registration(payload(), headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert len(harness.lead_rows()) == 1


def test_pending_replay_still_returns_202_with_the_same_token(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")
    headers = {"Idempotency-Key": "idem-pending-same"}

    first = harness.post_registration(payload(), headers=headers)
    second = harness.post_registration(payload(), headers=headers)

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json() == second.json()
    assert first.json()["tracking_token"] == second.json()["tracking_token"]
    assert len(harness.lead_rows()) == 1


def test_pending_replay_with_a_different_body_is_refused(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")
    headers = {"Idempotency-Key": "idem-pending-diff"}

    first = harness.post_registration(payload(phone="0912000011"), headers=headers)
    assert first.status_code == 202

    second = harness.post_registration(payload(phone="0912000012"), headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert first.json()["tracking_token"] not in second.text


def test_fingerprint_is_stored_on_the_lead(harness: Harness) -> None:
    from app.schemas import RegistrationCreate
    from app.services.registration import fingerprint_payload

    body = harness.post_registration(headers={"Idempotency-Key": "fp-1"}).json()
    lead = harness.lead_row(body["lead_id"])

    assert lead.request_fingerprint is not None
    assert len(lead.request_fingerprint) == 64
    expected = fingerprint_payload(RegistrationCreate.model_validate(payload()))
    assert lead.request_fingerprint == expected


def test_fingerprint_does_not_depend_on_the_password() -> None:
    """A hash of a password is still password-derived material."""
    from app.schemas import RegistrationCreate
    from app.services.registration import fingerprint_payload

    one = fingerprint_payload(RegistrationCreate.model_validate(payload(password="password-one-1")))
    two = fingerprint_payload(RegistrationCreate.model_validate(payload(password="password-two-2")))
    assert one == two
    assert "password-one-1" not in one
