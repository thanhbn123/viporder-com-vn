"""Idempotency.

A retried POST (double-click, flaky network, a client retry loop) must produce
one lead row and the same response body — not a second customer.
"""

from __future__ import annotations

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
