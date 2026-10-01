"""The password must not be persisted, logged, or traced — proven, not asserted.

This is the gate's hardest constraint, so the test attacks it from three
independent angles rather than trusting the code to behave:

(a) **The database file bytes.** After a successful registration the SQLite file
    (and its WAL sidecars) are read as raw bytes and searched for the password.
    Not "the ORM has no password column" — the actual bytes on disk.
(b) **Captured log output.** Every log record produced during the request must
    be free of the password, including one deliberately logged by this test and
    one that a naive ``logger.info(body)`` would have produced.
(c) **The response body**, on the success path, the validation-error path and
    the status path.

A distinctive, high-entropy password is used so a hit cannot be a coincidence
of a short random substring.
"""

from __future__ import annotations

import logging

from app.logging_filters import (
    PasswordRedactionFilter,
    clear_secrets,
    register_secret,
    scrub_text,
)
from tests.conftest import Harness, payload

#: Long and distinctive: this value is a CANARY for the "password is never
#: persisted" tests, not a credential. gitleaks' generic-api-key rule matches
#: the `PASSWORD = "..."` shape, so the line carries an inline allow.
#:
#: The allow is LINE-SCOPED, which was verified rather than assumed: with this
#: comment in place, a realistic AWS key appended to this same file is still
#: reported (line 238 in the probe), while only this canary is suppressed.
PASSWORD = "Zq7-CORRECT-HORSE-BATTERY-9xK"  # gitleaks:allow

#: The bytes a naive implementation would have written.
PASSWORD_BYTES = PASSWORD.encode("utf-8")


def _database_bytes(harness: Harness) -> bytes:
    """Raw bytes of every SQLite file backing this harness."""
    harness.database.engine.dispose()  # flush the WAL back into the main file
    base = harness.db_path()
    blob = b""
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = base.with_name(base.name + suffix)
        if candidate.exists():
            blob += candidate.read_bytes()
    return blob


def test_password_is_absent_from_the_sqlite_file(harness: Harness) -> None:
    response = harness.post_registration(payload(password=PASSWORD, phone="0912000011"))
    assert response.status_code == 201

    blob = _database_bytes(harness)

    assert PASSWORD_BYTES not in blob
    # Sanity check: the search is meaningful — the file really does contain the
    # lead, so a "not found" is not just an empty file.
    assert b"+84912000011" in blob
    assert b"REGISTERED" in blob


def test_password_is_absent_from_the_sqlite_file_on_a_pending_lead(
    make_harness,
) -> None:
    harness = make_harness(mock_provider_behaviour="unavailable")
    response = harness.post_registration(payload(password=PASSWORD))
    assert response.status_code == 202

    blob = _database_bytes(harness)
    assert PASSWORD_BYTES not in blob
    assert b"+84912345678" in blob


def test_password_is_absent_from_captured_logs(harness: Harness, caplog) -> None:
    """Everything logged during and just after a registration.

    Scope of the claim: this asserts over the log records this test observed.
    The first three lines are the real signal (the request itself). The last
    three are a stress test of the redactor — they deliberately make the mistake
    the control exists to survive, and they only pass because the password is
    still in the redactor's bounded cache.

    Known limitation, stated plainly: a password that some code formats into a
    message with no ``password`` marker, after it has fallen out of that cache
    (more than 64 newer passwords), would not be scrubbed. Closing that would
    mean keeping every password for the process lifetime, which is worse.
    """
    caplog.set_level(logging.DEBUG)

    response = harness.post_registration(payload(password=PASSWORD))
    assert response.status_code == 201

    # A handler that logs the raw request body — the exact mistake this control
    # exists to survive. The filter must scrub it even though our own code
    # never does this.
    logging.getLogger("test.naive").info(
        "incoming payload: %s", {"phone": "0912000011", "password": PASSWORD}
    )
    logging.getLogger("test.naive").info("password=%s", PASSWORD)
    logging.getLogger("test.naive").info(f"f-string body contains {PASSWORD} here")
    logging.getLogger("test.naive").info("MARKER-capture-is-live")

    # Positive control: capture really is working and really does see these
    # records and the request's own logs. Without this, "PASSWORD not in
    # caplog.text" could pass simply because caplog captured nothing at all.
    assert "MARKER-capture-is-live" in caplog.text
    assert any(r.name == "app.services.registration" for r in caplog.records)
    assert "<redacted>" in caplog.text

    for record in caplog.records:
        assert PASSWORD not in record.getMessage(), record.name
    assert PASSWORD not in caplog.text


def test_password_never_appears_in_the_response_body(harness: Harness) -> None:
    response = harness.post_registration(payload(password=PASSWORD))
    assert response.status_code == 201
    assert PASSWORD not in response.text
    assert PASSWORD.encode() not in response.content


def test_password_never_appears_in_an_error_response(harness: Harness) -> None:
    for body in (
        payload(password=PASSWORD, consent=False),
        payload(password=PASSWORD, phone="not-a-phone"),
        payload(password=PASSWORD, service_interest="nope"),
    ):
        response = harness.post_registration(body)
        assert response.status_code == 422
        assert PASSWORD not in response.text
        assert PASSWORD not in str(response.json())


def test_password_never_appears_in_the_status_endpoint(harness: Harness) -> None:
    created = harness.post_registration(payload(password=PASSWORD)).json()
    lead = harness.lead_row(created["lead_id"])

    response = harness.client.get(
        f"/api/v1/registrations/{lead.lead_id}",
        headers={"X-Tracking-Token": lead.tracking_token},
    )
    assert PASSWORD not in response.text


def test_lead_model_has_no_password_column() -> None:
    """A structural check: a future contributor cannot quietly add one."""
    from app.models import Lead

    columns = set(Lead.__table__.columns.keys())
    assert not {c for c in columns if "password" in c.lower()}
    assert "password" not in columns


def test_provider_failure_traceback_does_not_leak_the_password(make_harness, caplog) -> None:
    """An exception raised while holding the password must not print it.

    The provider's local variables include the plaintext password; Python's
    default traceback formatting prints source lines, not locals — and the
    redacting formatter is the second line of defence.
    """
    from app.providers.base import ProviderConfigurationError

    class ExplodingProvider:
        name = "exploding"

        def register(self, request):  # type: ignore[no-untyped-def]
            raise ProviderConfigurationError(f"provider exploded {request.phone}")

    harness = make_harness(provider=ExplodingProvider())
    caplog.set_level(logging.DEBUG)
    response = harness.post_registration(payload(password=PASSWORD))

    assert response.status_code == 202
    assert PASSWORD not in response.text
    assert PASSWORD not in caplog.text


# --- the redaction filter itself -------------------------------------------


def test_scrub_text_replaces_registered_secrets() -> None:
    clear_secrets()
    register_secret(PASSWORD)
    try:
        assert PASSWORD not in scrub_text(f"body={PASSWORD}")
        assert scrub_text(f"body={PASSWORD}") == "body=<redacted>"
    finally:
        clear_secrets()


def test_scrub_text_rewrites_password_keys() -> None:
    clear_secrets()
    scrubbed = scrub_text('{"password": "anything-here", "user": "a"}')
    assert "anything-here" not in scrubbed
    assert "<redacted>" in scrubbed


def test_short_values_are_not_registered_as_secrets() -> None:
    """Registering a 3-character 'secret' would mangle every log line."""
    clear_secrets()
    register_secret("abc")
    try:
        assert scrub_text("abc is a common substring") == "abc is a common substring"
    finally:
        clear_secrets()


def test_filter_scrubs_dict_args() -> None:
    clear_secrets()
    register_secret(PASSWORD)
    try:
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, "payload=%s", ({"password": PASSWORD},), None
        )
        assert PasswordRedactionFilter().filter(record) is True
        rendered = record.getMessage()
        assert PASSWORD not in rendered
        assert "<redacted>" in rendered
    finally:
        clear_secrets()


def test_secret_cache_is_bounded() -> None:
    """Holding every password forever would be its own problem."""
    from app.logging_filters import MAX_REMEMBERED_SECRETS, _current_secrets

    clear_secrets()
    try:
        for index in range(MAX_REMEMBERED_SECRETS + 10):
            register_secret(f"password-number-{index:04d}")
        assert len(_current_secrets()) == MAX_REMEMBERED_SECRETS
        # The oldest ones are gone, the newest are kept.
        assert "password-number-0000" not in _current_secrets()
        assert f"password-number-{MAX_REMEMBERED_SECRETS + 9:04d}" in _current_secrets()
    finally:
        clear_secrets()
