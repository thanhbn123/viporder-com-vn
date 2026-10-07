"""G12 — the one-shot live-registration tool must not be able to send twice.

WHY THIS FILE EXISTS. The owner authorized **exactly one** POST to the provider's
production registration API, and said that even a network ambiguity counts as
consumed. Before spending that budget, the tool was verified — and it **failed
verification**: `urllib` follows redirects by default, so a `30x` would have
produced a **second HTTP request**, to a location the provider chose.

These tests count requests at a real local HTTP server. They do not read the
source and infer behaviour; they make the tool talk to something that counts.

The tool is run as a **subprocess** on purpose: that is how an operator runs it, so
the test exercises the same path including its exit code.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
TOOL = ROOT / "tools" / "test_live_registration.py"

#: Deliberately unlike any real credential. Never used against a real endpoint —
#: every test here points the tool at a local server on 127.0.0.1.
FAKE_SECRET = "test-only-secret-value-0123456789"

#: Vietnamese ON PURPOSE. The same password can reach the tool as NFC or NFD, and
#: it can be JSON-escaped as ``\\uXXXX`` in either `ensure_ascii` mode. A literal
#: `str.replace(secret, ...)` misses those forms and has leaked a Vietnamese
#: password before; this value is the regression canary for that.
VIETNAMESE_SECRET = "Mật-khẩu-Kiểm-Thử-0123456789"

IDENTITY = {
    "LIVE_REG_NAME": "VIPORDER NGHIEM THU",
    "LIVE_REG_PHONE": "0968961962",
    "LIVE_REG_EMAIL": "qq968961962@gmail.com",
}


class _Recorder(http.server.BaseHTTPRequestHandler):
    """Counts requests and answers with a scripted response."""

    requests: list[tuple[str, str]] = []
    status = 200
    body = b'{"ok": true}'
    location: str | None = None

    def _handle(self) -> None:  # noqa: D102
        type(self).requests.append((self.command, self.path))
        self.send_response(type(self).status)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    do_POST = _handle
    do_GET = _handle

    def log_message(self, *args: object) -> None:  # noqa: D102
        pass


@pytest.fixture
def server():
    """A local HTTP server that counts requests. Redirects point back at itself."""
    _Recorder.requests = []
    _Recorder.status = 200
    _Recorder.location = None
    _Recorder.body = b'{"ok": true}'

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", _Recorder
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _evidence_dir(tmp_path_factory, monkeypatch):
    """Every run writes its evidence into a temp dir, never into the repository.

    The tool defaults to `var/live-registration/` (git-ignored). A test must not
    depend on the developer's checkout being writable there, and must not leave
    records behind, so the default is redirected for the whole module.
    """
    directory = tmp_path_factory.mktemp("live-registration-evidence")
    monkeypatch.setenv("LIVE_REG_EVIDENCE_DIR", str(directory))
    return directory


def _password_forms(secret: str) -> list[str]:
    """Every form the password could take in a record, enumerated independently.

    The test builds the response, so it knows exactly which strings it sent; the
    assertion is that NONE of them survives. Kept separate from the tool's own
    redaction so the test is not just a mirror of the code under test.
    """
    forms: list[str] = []
    for normalised in (
        secret,
        unicodedata.normalize("NFC", secret),
        unicodedata.normalize("NFD", secret),
        unicodedata.normalize("NFKC", secret),
        unicodedata.normalize("NFKD", secret),
    ):
        for form in (
            normalised,
            json.dumps(normalised, ensure_ascii=True)[1:-1],
            json.dumps(normalised, ensure_ascii=False)[1:-1],
        ):
            if form and form not in forms:
                forms.append(form)
    return forms


def _run(base: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **IDENTITY,
        "LIVE_REG_PASSWORD": FAKE_SECRET,
        "ENABLE_LIVE_REGISTRATION_TEST": "yes",
        "LIVE_REG_BASE_URL": base,
        **env_overrides,
    }
    return subprocess.run(
        [sys.executable, str(TOOL)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# One request, and redirects do not become a second one
# ---------------------------------------------------------------------------


def test_a_successful_run_sends_exactly_one_request(server) -> None:
    base, recorder = server
    result = _run(base)
    assert len(recorder.requests) == 1, recorder.requests
    assert recorder.requests[0][0] == "POST"
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_30x_does_NOT_produce_a_second_request(server) -> None:
    """The gap that stopped the authorized POST from being sent at all.

    `urllib` follows redirects by default. If this test ever fails, the tool can
    spend one authorized POST and turn it into two requests to an address the owner
    never approved.
    """
    base, recorder = server
    _Recorder.status = 302
    _Recorder.location = f"{base}/somewhere-else"

    result = _run(base)

    assert len(recorder.requests) == 1, (
        f"a redirect was followed: {recorder.requests} — the one-POST budget "
        f"would have bought two requests"
    )
    assert result.returncode == 1, result.stdout
    assert "redirect" in result.stdout.lower(), result.stdout


# ---------------------------------------------------------------------------
# Refusals happen BEFORE anything is transmitted
# ---------------------------------------------------------------------------


def test_a_short_password_is_refused_without_transmitting(server) -> None:
    base, recorder = server
    result = _run(base, LIVE_REG_PASSWORD="short")
    assert recorder.requests == [], "a request was sent despite a failed check"
    assert result.returncode == 2


def test_the_wrong_phone_is_refused_without_transmitting(server) -> None:
    base, recorder = server
    result = _run(base, LIVE_REG_EXPECT_PHONE="0968961962", LIVE_REG_PHONE="0900000000")
    assert recorder.requests == []
    assert result.returncode == 2
    assert "phone" in result.stdout.lower()


def test_the_wrong_email_is_refused_without_transmitting(server) -> None:
    base, recorder = server
    result = _run(
        base, LIVE_REG_EXPECT_EMAIL="qq968961962@gmail.com", LIVE_REG_EMAIL="x@example.com"
    )
    assert recorder.requests == []
    assert result.returncode == 2


def test_identity_matching_the_authorization_is_accepted(server) -> None:
    """The positive control for the three refusals above."""
    base, recorder = server
    result = _run(
        base,
        LIVE_REG_EXPECT_PHONE="0968961962",
        LIVE_REG_EXPECT_EMAIL="qq968961962@gmail.com",
    )
    assert len(recorder.requests) == 1
    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# The wire format, and the secret
# ---------------------------------------------------------------------------


def test_the_body_uses_exactly_the_provider_field_names(server) -> None:
    base, recorder = server
    captured: dict[str, object] = {}

    original = _Recorder.do_POST

    def spy(self):  # type: ignore[no-untyped-def]
        length = int(self.headers.get("Content-Length", 0))
        captured["raw"] = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    _Recorder.do_POST = spy
    try:
        _run(base)
    finally:
        _Recorder.do_POST = original

    body = json.loads(captured["raw"])
    assert list(body) == [
        "name",
        "phone",
        "email",
        "password",
        "confirmPassword",
        "acceptTerms",
    ], list(body)
    assert body["confirmPassword"] == body["password"], "the confirmation must match"
    assert body["acceptTerms"] is True, "acceptTerms must be exactly true"
    assert body["phone"] == "0968961962"
    assert body["email"] == "qq968961962@gmail.com"


def test_the_password_is_never_printed(server) -> None:
    """Not on success, not on failure, not even when the provider echoes it."""
    base, _ = server
    _Recorder.body = (
        b'{"echo": "test-only-secret-value-0123456789", "note": "provider echoed the password"}'
    )
    result = _run(base)
    combined = result.stdout + result.stderr
    assert FAKE_SECRET not in combined, "the password reached the terminal"
    assert "<REDACTED>" in combined, "the echo was not redacted"


def test_without_the_flag_nothing_is_sent(server) -> None:
    base, recorder = server
    env = {
        **os.environ,
        **IDENTITY,
        "LIVE_REG_PASSWORD": FAKE_SECRET,
        "LIVE_REG_BASE_URL": base,
    }
    env.pop("ENABLE_LIVE_REGISTRATION_TEST", None)
    result = subprocess.run(
        [sys.executable, str(TOOL)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert recorder.requests == []
    assert result.returncode == 2
    assert "ENABLE_LIVE_REGISTRATION_TEST" in result.stdout


# ---------------------------------------------------------------------------
# The run keeps its own evidence, in a file, on success AND on failure
# ---------------------------------------------------------------------------


def _evidence_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"))


def test_a_2xx_run_writes_a_readable_evidence_file(server, tmp_path) -> None:
    """The record the operator is told about must exist, be readable, and hold
    the measured facts — status code, content-type, size, elapsed time, the key
    names, and a timezone-carrying timestamp."""
    base, _ = server
    _Recorder.status = 201
    _Recorder.body = b'{"status":"ok","data":{"customer_code":"TT00123"}}'

    result = _run(base, LIVE_REG_EVIDENCE_DIR=str(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    files = _evidence_files(tmp_path)
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    assert "201" in text, "the status code is not in the record"
    assert "customer_code" in text, "the response key names are not in the record"
    assert "content_type" in text
    assert "elapsed_seconds" in text
    assert "recorded_at" in text
    # A timestamp with NO timezone is how this project has drawn the wrong
    # conclusion three times; the record must carry the offset.
    assert '"recorded_at": "' in text
    assert ("+0" in text) or ("+7" in text) or ("Z" in text), text

    # The path is printed BEFORE the POST, not after the reply. Compare the two
    # line positions in the actual stdout.
    assert str(files[0].resolve()) in result.stdout, result.stdout
    assert result.stdout.index("evidence   :") < result.stdout.index("HTTP status"), result.stdout


def test_the_evidence_file_never_contains_the_password_in_any_escape_form(server, tmp_path) -> None:
    """Raw `str.replace` leaked a Vietnamese password once. The response here
    echoes NFC, NFD, NFKC, NFKD and both JSON `ensure_ascii` modes; NONE may
    survive into the record, in characters or in UTF-8 bytes."""
    base, _ = server
    forms = _password_forms(VIETNAMESE_SECRET)
    _Recorder.body = json.dumps({"echo": forms}, ensure_ascii=False).encode("utf-8")

    result = _run(
        base,
        LIVE_REG_PASSWORD=VIETNAMESE_SECRET,
        LIVE_REG_EVIDENCE_DIR=str(tmp_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    files = _evidence_files(tmp_path)
    assert len(files) == 1, files
    raw = files[0].read_bytes()
    text = raw.decode("utf-8")
    for form in forms:
        assert form not in text, f"the password leaked in this form: {form!r}"
        assert form.encode("utf-8") not in raw, f"the password leaked as bytes: {form!r}"
    assert "<REDACTED>" in text, "nothing was marked as redacted"
    assert VIETNAMESE_SECRET not in result.stdout + result.stderr


def test_a_failed_run_still_writes_an_evidence_file(server, tmp_path) -> None:
    """A failed attempt is still evidence: a 5xx must leave a record behind."""
    base, _ = server
    _Recorder.status = 500
    _Recorder.body = b'{"error":"provider exploded"}'

    result = _run(base, LIVE_REG_EVIDENCE_DIR=str(tmp_path))

    assert result.returncode == 1, result.stdout
    files = _evidence_files(tmp_path)
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    assert "500" in text
    assert "http_response" in text


def test_a_redirect_refusal_is_also_recorded(server, tmp_path) -> None:
    base, _ = server
    _Recorder.status = 302
    _Recorder.location = f"{base}/elsewhere"

    result = _run(base, LIVE_REG_EVIDENCE_DIR=str(tmp_path))

    assert result.returncode == 1, result.stdout
    files = _evidence_files(tmp_path)
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    assert "redirect_refused" in text
    assert "elsewhere" in text


def test_a_non_json_body_is_also_recorded(server, tmp_path) -> None:
    base, _ = server
    _Recorder.status = 200
    _Recorder.body = b"<html>not json at all</html>"

    result = _run(base, LIVE_REG_EVIDENCE_DIR=str(tmp_path))

    assert result.returncode == 1, result.stdout
    files = _evidence_files(tmp_path)
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    assert '"body_is_json": false' in text, text


def test_a_transport_failure_is_also_recorded(tmp_path) -> None:
    """No server is listening, so the POST cannot complete — and that attempt,
    which may still have reached the provider, must be on disk."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    result = _run(f"http://127.0.0.1:{port}", LIVE_REG_EVIDENCE_DIR=str(tmp_path))

    assert result.returncode == 1, result.stdout
    files = _evidence_files(tmp_path)
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    assert "transport_failure" in text
