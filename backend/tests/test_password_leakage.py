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

import io
import logging
import sys

import pytest
from pydantic import ValidationError

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


def _requires_sqlite(harness: Harness) -> None:
    """Skip the on-disk scan when this run is not SQLite.

    These two tests read the raw database FILE to prove the password is not in it.
    That scan is only meaningful for SQLite, where the whole store is one file the
    test can read. On PostgreSQL the equivalent evidence is different — the column
    does not exist at all — and it is covered by `test_postgres.py`.

    WHY THE GUARD EXISTS: before the shared harness could run on PostgreSQL, these
    two tests never executed there, so the problem could not appear. Making the
    suite engine-agnostic surfaced them as two failures in a run that was
    otherwise green — which is the guard doing its job.
    """
    if harness.database.engine.dialect.name != "sqlite":
        pytest.skip(
            f"raw-file scan needs SQLite; this run is "
            f"{harness.database.engine.dialect.name!r} (column absence is covered by test_postgres.py)"
        )


def test_password_is_absent_from_the_sqlite_file(harness: Harness) -> None:
    _requires_sqlite(harness)
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
    _requires_sqlite(harness)
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
    # The confirmation is in the same class of value and gets the same check, so
    # "we added the field the brief asked for" cannot become "we added a column".
    assert not {c for c in columns if "confirm" in c.lower()}
    assert "confirm_password" not in columns


def test_confirm_password_cannot_differ_from_the_password_on_the_live_path() -> None:
    """The two secrets are always the SAME string on the live path, and that matters.

    It means the byte-level and log-level guarantees already proven for the
    password cover the confirmation as well — there is no second distinct secret
    to hunt for. Stated as a measurement rather than a hope: the schema refuses a
    mismatch, so no live request can carry two different values.
    """
    from app.schemas import RegistrationCreate

    ok = RegistrationCreate.model_validate(
        payload(password="aaaaaaaa-1", confirm_password="aaaaaaaa-1")
    )
    assert ok.password.get_secret_value() == ok.confirm_password.get_secret_value()

    with pytest.raises(ValidationError):
        RegistrationCreate.model_validate(
            payload(password="aaaaaaaa-1", confirm_password="bbbbbbbb-2")
        )


def test_a_provider_echoing_the_confirmation_does_not_leak_it(make_harness, caplog) -> None:
    """A provider that raises with ``request.confirm_password`` in the message.

    ``RegistrationRequest.__post_init__`` refuses a mismatch and the schema
    refuses it before that, so a DISTINCT confirmation value is unreachable — which
    is why this uses the real, equal value. What it proves is the part that is
    reachable and was worth proving: the *confirmation field* is registered with
    the log redactor in its own right, so the traceback is scrubbed even though the
    provider read ``confirm_password`` rather than ``password``.
    """
    # gitleaks:allow — a test canary, not a credential.
    canary = "CONFIRM-canary-9876"

    class EchoingProvider:
        name = "confirm-echoing"

        def register(self, request):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"upstream rejected confirmation {request.confirm_password}")

    harness = make_harness(provider=EchoingProvider())
    caplog.set_level(logging.DEBUG)

    response = harness.post_registration(payload(password=canary, confirm_password=canary))

    # A provider that raises leaves the lead PENDING — a retained lead, not a 500.
    assert response.status_code == 202, response.text

    # Positive controls: the traceback really was produced and really was captured.
    assert "upstream rejected confirmation" in caplog.text
    assert "<redacted>" in caplog.text
    assert canary not in caplog.text
    assert canary not in response.text
    for record in caplog.records:
        assert canary not in (record.exc_text or "")


def test_the_request_refuses_a_mismatch_the_schema_could_not_see() -> None:
    """The last line before the wire, for a request built without the schema."""
    from app.providers.base import RegistrationRequest

    with pytest.raises(ValueError, match="confirm_password"):
        RegistrationRequest(
            full_name="A",
            phone="+84912345678",
            password="aaaaaaaa-1",
            confirm_password="bbbbbbbb-2",
            accept_terms=True,
        )


class PasswordEchoingProvider:
    """A provider that puts the password into its exception message.

    This is the shape the verifier used to falsify the first version of this
    control: the password reaches the *traceback*, not the log message, so
    scrubbing ``record.msg`` alone was not enough.
    """

    name = "password-echoing"

    def register(self, request):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"upstream rejected credentials for {request.password}")


def test_provider_failure_traceback_does_not_leak_the_password(make_harness, caplog) -> None:
    """An exception raised while holding the password must not print it.

    The provider's exception message and its local variables both contain the
    plaintext password. Rendered tracebacks are scrubbed at the *record*, not in
    a formatter, so this holds for any handler.
    """
    harness = make_harness(provider=PasswordEchoingProvider())
    caplog.set_level(logging.DEBUG)
    response = harness.post_registration(payload(password=PASSWORD))

    assert response.status_code == 202
    assert PASSWORD not in response.text

    # Positive control: the traceback really was logged and really did reach
    # caplog. Without this, "PASSWORD not in caplog.text" could pass because
    # nothing was captured at all.
    assert "Traceback (most recent call last)" in caplog.text
    assert "upstream rejected credentials for" in caplog.text
    assert "<redacted>" in caplog.text

    assert PASSWORD not in caplog.text
    for record in caplog.records:
        assert PASSWORD not in record.getMessage()
        assert PASSWORD not in (record.exc_text or "")


def test_exception_traceback_is_scrubbed_before_any_handler_formats_it() -> None:
    """The record itself must carry no readable password — unit level.

    A handler that formats the record itself bypasses any formatter we install,
    so the guarantee has to live on the record.
    """
    from app.logging_filters import scrub_record

    try:
        raise RuntimeError(f"rejected credentials for {PASSWORD}")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "test.traceback", logging.ERROR, __file__, 1, "registration failed", (), exc_info
    )
    scrub_record(record)

    assert record.exc_info is None, "exc_info must be cleared so no formatter can render it"
    assert getattr(record, "exc_info_scrubbed", False) is True
    assert record.exc_text is not None
    assert PASSWORD not in record.exc_text
    assert "rejected credentials for" in record.exc_text
    # And through a plain formatter — the kind any third-party handler would use.
    assert PASSWORD not in logging.Formatter().format(record)


def test_password_does_not_leak_through_a_handler_registered_before_the_app(
    make_harness,
) -> None:
    """A library installing a root handler before `create_app()`.

    That is an ordinary habit, and it produces a handler this application never
    configured and cannot install a formatter on. Redaction therefore has to
    happen at record creation.
    """
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        harness = make_harness(provider=PasswordEchoingProvider())
        response = harness.post_registration(payload(password=PASSWORD))
    finally:
        root.removeHandler(handler)
        handler.close()

    captured = buffer.getvalue()
    assert response.status_code == 202

    # Positive control: this foreign handler really did receive the traceback.
    assert "Traceback (most recent call last)" in captured
    assert "upstream rejected credentials for" in captured
    assert "<redacted>" in captured

    assert PASSWORD not in captured


def test_basicconfig_handler_is_covered_too(make_harness) -> None:
    """The same hole, via the exact API a library would use."""
    buffer = io.StringIO()
    root = logging.getLogger()
    before = list(root.handlers)
    logging.basicConfig(level=logging.DEBUG, stream=buffer, force=True)
    try:
        harness = make_harness(provider=PasswordEchoingProvider())
        harness.post_registration(payload(password=PASSWORD))
    finally:
        for added in [h for h in root.handlers if h not in before]:
            root.removeHandler(added)
        root.handlers[:] = before

    captured = buffer.getvalue()
    # `force=True` guarantees basicConfig installed its own handler, so the
    # positive controls cannot pass vacuously on an empty buffer.
    assert "Traceback (most recent call last)" in captured
    assert "upstream rejected credentials for" in captured
    assert PASSWORD not in captured


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


# ---------------------------------------------------------------------------
# ESCAPED ECHOES — the byte-scan above CANNOT see these, which is the point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        'Mat"khau-2026-x9',  # a double quote -> JSON-escaped as \"
        "pa\\ss\\word",  # backslashes -> JSON-escaped as \\
        "mậtkhẩu1",  # non-ASCII -> JSON may emit \uXXXX
        "line\nbreak9",  # newline -> \n
        "tab\tsecret9",  # tab -> \t
        "plain-secret-123",  # the control: an ordinary password
    ],
    ids=["quote", "backslash", "vietnamese", "newline", "tab", "plain"],
)
def test_an_ESCAPED_echo_of_the_password_is_redacted(password: str) -> None:
    """A FINDING FROM ADVERSARIAL REVIEW, and it was a real leak.

    `_redacted_excerpt` used a plain `str.replace(secret, ...)`, but an upstream that
    echoes the request emits the **escaped** form. Measured before the fix::

        body   '{"error":"password pa\\"ss\\\\word rejected"}'
        secret 'pa"ss\\word'
        plain replace -> NOTHING redacted

    So any password containing a quote, a backslash, a newline — or **any non-ASCII
    character, i.e. most Vietnamese passwords** — was written to
    `leads.last_error_message` and recoverable with one `json.loads`.

    The byte-scan in the test above cannot catch it, because the stored bytes differ
    from the plaintext. So this test does what an attacker would: it PARSES the
    persisted text and looks for the secret inside.
    """
    import json

    from app.providers.khaibao9610 import _redacted_excerpt

    body = json.dumps({"error": f"invalid credentials for {password}"})
    # The escaped form really is present, so this is not a vacuous test.
    assert password != body, "the body must actually be an escaped echo"

    stored = _redacted_excerpt(body, password)

    # A VACUOUS VERSION OF THIS TEST SHIPPED FOR ONE COMMIT. It checked
    # `json.dumps(json.loads(stored))` — but `json.dumps` RE-ESCAPES, so the
    # plaintext was never visible to the assertion and the test passed even with the
    # redaction removed. The negative control caught it.
    #
    # The honest check is what an attacker does: decode the stored text and read the
    # DECODED string values.
    def decoded_strings(obj: object):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for value in obj.values():
                yield from decoded_strings(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from decoded_strings(value)

    decoded = " | ".join(decoded_strings(json.loads(stored)))
    assert password not in decoded, f"RECOVERABLE from the stored text: {stored!r}"


def test_an_escaped_echo_does_not_survive_via_unicode_escape() -> None:
    """The `\\uXXXX` spelling must be redacted as well as the raw character."""
    import json

    from app.providers.khaibao9610 import _redacted_excerpt

    password = "mậtkhẩu1"
    stored = _redacted_excerpt(
        json.dumps({"error": f"password {password}"}, ensure_ascii=True), password
    )
    assert password not in stored
    assert "\\u" not in stored, f"an unredacted escape sequence remains: {stored!r}"
