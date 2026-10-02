"""An idempotent replay must be a replay, not a reconstruction.

The contract is "same ``Idempotency-Key``, same body -> **the identical stored
response**". Reconstructing the response from the lead's current row state looks
equivalent and is not:

* a lead whose provider outcome was DUPLICATE is stored ``FAILED``, and the
  original request answers ``409``. Reconstructed, the replay answered ``202``
  with the *pending* body — a different status code for the same request;
* that pending body said "we could not confirm it yet… we will complete it
  shortly", which is false for a ``FAILED`` lead. Nothing was going to complete
  it;
* ``FAILED`` and ``PENDING`` collapsed into one branch, so "we gave up" and "we
  kept it, try later" became indistinguishable.

So the response is now stored on the lead when the attempt reaches a terminal
outcome, and replayed verbatim. These tests assert the **status code** as well as
the body — the earlier replay tests checked bodies on the ``201``/``202`` paths
only, which is exactly why this survived.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.errors import DUPLICATE_PHONE, IDEMPOTENCY_KEY_REUSED, PROVIDER_INVALID
from app.models import RegistrationStatus
from tests.conftest import Harness, payload

KEY = "replay-fidelity-key"


def _post(harness: Harness, headers: dict | None = None, body: dict | None = None):  # type: ignore[no-untyped-def]
    return harness.post_registration(
        body if body is not None else payload(phone="0912000011"),
        headers=headers if headers is not None else {"Idempotency-Key": KEY},
    )


def _assert_identical(first, second) -> None:  # type: ignore[no-untyped-def]
    assert first.status_code == second.status_code, (
        f"status changed between the request and its replay: "
        f"{first.status_code} -> {second.status_code}"
    )
    assert first.json() == second.json(), "body changed between the request and its replay"


# --- the four outcomes ------------------------------------------------------


def test_duplicate_replays_as_409_with_a_byte_identical_body(make_harness) -> None:
    """The measured defect, end to end."""
    harness = make_harness(mock_provider_behaviour="duplicate")

    first = _post(harness)
    second = _post(harness)

    assert first.status_code == 409, first.text
    assert second.status_code == 409, second.text
    assert first.json() == second.json()
    assert first.json()["error"]["code"] == DUPLICATE_PHONE

    # And no third lead appeared.
    assert len(harness.lead_rows()) == 1


def test_duplicate_replay_does_not_claim_the_registration_is_pending(make_harness) -> None:
    """The specific falsehood: "we will complete it shortly" on a FAILED lead."""
    harness = make_harness(mock_provider_behaviour="duplicate")
    _post(harness)
    replay = _post(harness)

    assert replay.json()["error"]["code"] == DUPLICATE_PHONE
    assert "registration_status" not in replay.json()
    assert "tracking_token" not in replay.json()
    # These assert the PENDING wording is absent. They must name the wording that
    # actually exists: while the messages were English, translating them would
    # have left both lines passing VACUOUSLY — the strings would no longer appear
    # anywhere, so the assertions would check nothing while still going green.
    assert "hoàn tất trong" not in replay.text
    assert "chưa xác nhận được ngay" not in replay.text


def test_invalid_replays_as_422_with_a_byte_identical_body(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="invalid")

    first = _post(harness)
    second = _post(harness)

    assert first.status_code == 422, first.text
    assert second.status_code == 422, second.text
    assert first.json() == second.json()
    assert first.json()["error"]["code"] == PROVIDER_INVALID
    assert len(harness.lead_rows()) == 1


def test_registered_replays_as_201_with_a_byte_identical_body(harness: Harness) -> None:
    first = _post(harness)
    second = _post(harness)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert first.json()["registration_status"] == "REGISTERED"
    assert len(harness.lead_rows()) == 1


def test_pending_replays_as_202_with_the_same_token(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")

    first = _post(harness)
    second = _post(harness)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["registration_status"] == "PENDING"
    assert len(harness.lead_rows()) == 1


@pytest.mark.parametrize(
    ("behaviour", "expected_status"),
    [
        ("success", 201),
        ("unavailable", 202),
        ("timeout", 202),
        ("error", 202),
        ("duplicate", 409),
        ("invalid", 422),
    ],
)
def test_every_outcome_replays_with_its_own_status_code(
    make_harness, behaviour: str, expected_status: int
) -> None:
    harness = make_harness(mock_provider_behaviour=behaviour)

    first = _post(harness)
    second = _post(harness)

    assert first.status_code == expected_status, first.text
    _assert_identical(first, second)


# --- what is stored ---------------------------------------------------------


def test_a_terminal_outcome_stores_the_response_on_the_lead(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="duplicate")
    _post(harness)
    lead = harness.lead_rows()[0]

    assert lead.registration_status is RegistrationStatus.FAILED
    assert lead.response_status == 409
    assert lead.response_body == {
        "error": {"code": DUPLICATE_PHONE, "message": lead.response_body["error"]["message"]}
    }


def test_a_registered_lead_stores_its_201_response(harness: Harness) -> None:
    body = _post(harness).json()
    lead = harness.lead_row(body["lead_id"])

    assert lead.response_status == 201
    assert lead.response_body["lead_id"] == lead.lead_id
    assert lead.response_body["registration_status"] == "REGISTERED"


def test_a_pending_lead_stores_no_response(make_harness) -> None:
    """PENDING is not terminal: an admin retry may still change it."""
    harness = make_harness(mock_provider_behaviour="unavailable")
    _post(harness)
    lead = harness.lead_rows()[0]

    assert lead.registration_status is RegistrationStatus.PENDING
    assert lead.response_status is None
    assert lead.response_body is None


def test_the_stored_body_is_exactly_what_the_replay_returns(make_harness) -> None:
    """No reconstruction step: the reply IS the stored value."""
    harness = make_harness(mock_provider_behaviour="duplicate")
    _post(harness)
    lead = harness.lead_rows()[0]

    replayed = _post(harness)
    assert replayed.json() == lead.response_body


def test_a_later_success_replaces_the_stored_failure_response(make_harness) -> None:
    """Why PENDING must not keep a stale stored response."""
    from app.providers.base import ProviderStatus, RegistrationResult

    class FlakyProvider:
        name = "flaky"

        def __init__(self) -> None:
            self.calls = 0

        def register(self, request):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return RegistrationResult(
                    status=ProviderStatus.UNAVAILABLE,
                    message="down",
                    http_status=503,
                    retryable=True,
                    error_code="PROVIDER_UNAVAILABLE",
                )
            return RegistrationResult(
                status=ProviderStatus.SUCCESS,
                external_customer_id="ext-7",
                external_customer_code="TT00007",
                message="ok",
                http_status=201,
            )

    harness = make_harness(provider=FlakyProvider(), admin_api_token="admin-tok")

    first = _post(harness)
    assert first.status_code == 202
    lead_id = first.json()["lead_id"]

    retry = harness.client.post(
        f"/api/v1/admin/registrations/{lead_id}/retry",
        headers={"X-Admin-Token": "admin-tok"},
        json={"password": "secret-at-least-8"},
    )
    assert retry.status_code == 200
    assert retry.json()["registration_status"] == "REGISTERED"

    lead = harness.lead_row(lead_id)
    assert lead.response_status == 201
    assert lead.response_body["registration_status"] == "REGISTERED"

    # And the replay now reflects the new terminal state.
    replay = _post(harness)
    assert replay.status_code == 201, replay.text
    assert replay.json()["registration_status"] == "REGISTERED"


# --- fail-closed fallback for rows written before the columns existed -------


def _blank_stored_response(harness: Harness, lead_id: str) -> None:
    with harness.database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE leads SET response_status = NULL, response_body = NULL "
                "WHERE lead_id = :lead_id"
            ),
            {"lead_id": lead_id},
        )


def test_legacy_duplicate_row_is_derived_as_409_not_202(make_harness) -> None:
    """A row predating the columns must still replay with the right code."""
    harness = make_harness(mock_provider_behaviour="duplicate")
    _post(harness)
    _blank_stored_response(harness, harness.lead_rows()[0].lead_id)

    replayed = _post(harness)
    assert replayed.status_code == 409, replayed.text
    assert replayed.json()["error"]["code"] == DUPLICATE_PHONE


def test_legacy_invalid_row_is_derived_as_422(make_harness) -> None:
    harness = make_harness(mock_provider_behaviour="invalid")
    _post(harness)
    _blank_stored_response(harness, harness.lead_rows()[0].lead_id)

    replayed = _post(harness)
    assert replayed.status_code == 422, replayed.text
    assert replayed.json()["error"]["code"] == PROVIDER_INVALID


def test_a_failed_row_with_an_unrecognised_cause_fails_closed(make_harness) -> None:
    """Never guess 201/202 for a row we cannot explain."""
    harness = make_harness(mock_provider_behaviour="duplicate")
    _post(harness)
    lead = harness.lead_rows()[0]
    _blank_stored_response(harness, lead.lead_id)

    with harness.database.engine.begin() as connection:
        connection.execute(
            text("UPDATE leads SET last_error_code = 'SOMETHING_NEW' WHERE lead_id = :id"),
            {"id": lead.lead_id},
        )

    replayed = _post(harness)

    assert replayed.status_code == 409, replayed.text
    assert replayed.json()["error"]["code"] == "REGISTRATION_FAILED"
    # It must not pretend to be pending, and must not expose anything.
    assert "tracking_token" not in replayed.json()
    assert "external_customer_code" not in replayed.text


def test_a_legacy_registered_row_still_replays_as_201(make_harness) -> None:
    harness = make_harness()
    first = _post(harness)
    assert first.status_code == 201
    _blank_stored_response(harness, first.json()["lead_id"])

    replayed = _post(harness)
    assert replayed.status_code == 201
    assert replayed.json() == first.json()


def test_a_mismatched_body_is_still_refused_before_any_replay(make_harness) -> None:
    """The fingerprint check is unchanged by any of this."""
    harness = make_harness(mock_provider_behaviour="duplicate")
    _post(harness)

    other = harness.post_registration(payload(phone="0912000012"), headers={"Idempotency-Key": KEY})
    assert other.status_code == 409
    assert other.json()["error"]["code"] == IDEMPOTENCY_KEY_REUSED


def test_stored_response_body_is_json_round_trippable(make_harness) -> None:
    """The column is JSON, so a replay must not depend on object identity."""
    harness = make_harness(mock_provider_behaviour="duplicate")
    first = _post(harness)
    lead = harness.lead_rows()[0]

    # Force a fresh read from the database.
    harness.database.engine.dispose()
    reloaded = harness.lead_row(lead.lead_id)
    assert json.loads(json.dumps(reloaded.response_body)) == first.json()
