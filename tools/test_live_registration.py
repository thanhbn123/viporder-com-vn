#!/usr/bin/env python3
"""Execute exactly ONE controlled live registration against the real provider.

WHY THIS IS A SEPARATE TOOL AND NOT A TEST. Registering a customer is a WRITE on
somebody else's production system. It creates an account a real person could try to
sign in with. That is not something a test suite should be able to do by accident,
so this is not in `tests/` and pytest never collects it.

THREE INDEPENDENT THINGS MUST BE TRUE BEFORE IT WILL SEND ANYTHING:

  1. `ENABLE_LIVE_REGISTRATION_TEST=yes`          — an explicit, unmistakable flag;
  2. every field of a test identity, supplied through the environment;
  3. the endpoint is the configured base URL, and its host is not the marketing
     domain (a typo must not point this at the wrong service).

If any of them is missing it prints what is missing and exits 2 WITHOUT sending.

ONE POST PER INVOCATION. There is no retry loop and no batch mode. If it fails, a
person decides whether to run it again. Registering the same identity twice is how
you create a duplicate customer on somebody else's system.

IT WRITES ITS OWN EVIDENCE TO DISK, AND PRINTS THE PATH BEFORE THE POST.

The authorized POST of 2026-10-07 lost its raw body when the container was
recreated; the only copy was terminal scrollback. PR #65 patched the SERVER side.
This tool is the CLIENT side of the same fix: before the request is sent it prints
where the record will go, then records status code, content-type, byte count,
elapsed time, the sanitized body, the response's key names and a timezone-carrying
timestamp — SUCCESS OR FAILURE. A redirect refusal, a transport failure, a
non-JSON body and a 4xx/5xx are all results, and a failed attempt is still
evidence. The file is written through a same-directory temp file plus
`os.replace`, so a crash mid-write cannot truncate a previous record.

WHERE THE FILE GOES. Default `var/live-registration/` under the repository root,
which is git-ignored and outside tracker reach. Override with
`LIVE_REG_EVIDENCE_DIR` (a directory) or `LIVE_REG_EVIDENCE_FILE` (an exact path).

NEVER PRINTS AND NEVER STORES THE PASSWORD. Not on success, not on failure, not
inside an error body. Every response is passed through `_sanitize`, which redacts
every normalization and JSON-escape form of the password before anything is
written to the terminal or to the evidence file.

USAGE (the owner runs this; substitute real values):

    ENABLE_LIVE_REGISTRATION_TEST=yes \\
    LIVE_REG_NAME='Khách Kiểm Thử' \\
    LIVE_REG_PHONE='0900000000' \\
    LIVE_REG_EMAIL='test@example.com' \\
    LIVE_REG_PASSWORD='<a-real-test-password>' \\
    python3 tools/test_live_registration.py

Add `--base-url https://apiviporder.com/frontend/v1` to override the endpoint.

WHAT IT PRINTS: HTTP status, content type, elapsed time, the response's KEY NAMES
(never values blindly), a sanitized body with any password occurrence redacted and
long values truncated, and the evidence file path — before the POST is sent.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

ENABLE_FLAG = "ENABLE_LIVE_REGISTRATION_TEST"
DEFAULT_BASE = "https://apiviporder.com/frontend/v1"

#: Where evidence goes when the environment does not say otherwise. `var/` is in
#: `.gitignore` AND in the repo-hygiene forbidden-path list, so a record left here
#: cannot be committed even by a mistaken `git add -A`.
DEFAULT_EVIDENCE_DIR = ROOT / "var" / "live-registration"

#: Field names the provider's contract requires, in order. Pinned so a rename
#: here cannot silently change the wire format.
PROVIDER_FIELDS = (
    "name",
    "phone",
    "email",
    "password",
    "confirmPassword",
    "acceptTerms",
)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Raise on any 30x instead of following it.

    WHY THIS EXISTS. `urllib` follows redirects by DEFAULT. The owner authorized
    EXACTLY ONE POST; a 301/302/303/307 would have produced a SECOND request, to a
    location the provider chose rather than one we did. A redirect is therefore
    reported as an unexpected response and never chased.

    Verified by a test that counts requests at the transport, not by reading this
    comment.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"refusing to follow a redirect to {newurl}",
            headers,
            fp,
        )


REQUIRED_ENV = {
    "LIVE_REG_NAME": "name",
    "LIVE_REG_PHONE": "phone",
    "LIVE_REG_EMAIL": "email",
    "LIVE_REG_PASSWORD": "password",
}

#: Any response value longer than this is truncated rather than printed whole.
MAX_VALUE = 300


def _fail(missing: list[str]) -> int:
    print("REFUSING TO SEND. Missing:")
    for m in missing:
        print(f"  - {m}")
    print("\nNothing was sent. Read the module docstring for the exact invocation.")
    return 2


def _sanitize(value: object, secret: str, depth: int = 0, *, truncate: bool = True) -> object:
    """Redact every form of the password and truncate long values, recursively.

    THE ONLY REDACTION PATH IN THIS FILE. `str.replace(secret, ...)` alone is not
    enough: the same Vietnamese password can reach a record as NFC or NFD (or
    NFKC/NFKD), and it can reach it JSON-escaped as ``\\uXXXX`` in either
    `ensure_ascii` mode. A literal replacement misses those and has leaked a
    Vietnamese password before. So all normalizations and both JSON escape modes
    are redacted here — and this same function is run once more over the finished
    evidence text, so an escape introduced by the serializer cannot survive.
    """
    if depth > 6:
        return "<max depth>"
    if isinstance(value, dict):
        return {k: _sanitize(v, secret, depth + 1, truncate=truncate) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, secret, depth + 1, truncate=truncate) for v in value[:20]]
    if isinstance(value, str):
        out = value
        if secret:
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
            for form in sorted(forms, key=len, reverse=True):
                out = out.replace(form, "<REDACTED>")
        if truncate and len(out) > MAX_VALUE:
            return out[:MAX_VALUE] + "...<truncated>"
        return out
    return value


def _now_iso() -> str:
    """Local time WITH an explicit UTC offset — never a bare timestamp."""
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _evidence_path() -> Path:
    exact = os.environ.get("LIVE_REG_EVIDENCE_FILE", "").strip()
    if exact:
        return Path(exact).expanduser().resolve()
    directory = os.environ.get("LIVE_REG_EVIDENCE_DIR", "").strip()
    base = Path(directory).expanduser() if directory else DEFAULT_EVIDENCE_DIR
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (base / f"registration-evidence-{stamp}.json").resolve()


def _write_evidence(path: Path, report: dict[str, object], secret: str) -> None:
    """Record via a same-directory temp file, then rename it into place.

    §16.3: the rename is the atomic step. A crash before it leaves any existing
    record untouched; opening the real path with `'w'` would truncate it first and
    turn a failed write into lost evidence. The redaction is applied once more to
    the finished text, through the SAME `_sanitize`, so a JSON escape produced by
    the serializer is covered too.
    """
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    redacted = _sanitize(text, secret, truncate=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(redacted))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _report(
    *,
    outcome: str,
    endpoint: str,
    recorded_at: str,
    elapsed_seconds: float | None,
    status: int | None,
    content_type: str | None,
    raw_bytes: int | None,
    body_is_json: bool,
    key_names: list[str],
    body: object,
    password_echoed: bool,
    note: str,
) -> dict[str, object]:
    """The on-disk contract of one attempt. Keys are stable so a later run diffs."""
    return {
        "tool": "tools/test_live_registration.py",
        "recorded_at": recorded_at,
        "outcome": outcome,
        "endpoint": endpoint,
        "http_status": status,
        "content_type": content_type,
        "elapsed_seconds": None if elapsed_seconds is None else round(elapsed_seconds, 3),
        "bytes": raw_bytes,
        "body_is_json": body_is_json,
        "key_names": key_names,
        "body_sanitized": body,
        "password_echoed": password_echoed,
        "note": note,
    }


def _response_keys(o: object, prefix: str = "", depth: int = 0) -> list[str]:
    """The response's key names, structure only, never the values."""
    if depth > 4:
        return []
    if isinstance(o, dict):
        out = []
        for k, v in o.items():
            out.append(f"{prefix}{k}")
            out.extend(_response_keys(v, f"{prefix}{k}.", depth + 1))
        return out
    if isinstance(o, list) and o:
        return _response_keys(o[0], f"{prefix}[].", depth + 1)
    return []


def main() -> int:
    if os.environ.get(ENABLE_FLAG, "").strip().lower() not in ("yes", "true", "1"):
        return _fail([f"{ENABLE_FLAG}=yes   (this is a WRITE against a live provider)"])

    missing = [k for k in REQUIRED_ENV if not os.environ.get(k, "").strip()]
    if missing:
        return _fail([f"{k}  ({REQUIRED_ENV[k]})" for k in missing])

    base = (os.environ.get("LIVE_REG_BASE_URL") or DEFAULT_BASE).rstrip("/")
    args = sys.argv[1:]
    if "--base-url" in args:
        base = args[args.index("--base-url") + 1].rstrip("/")

    host = base.split("//", 1)[-1].split("/")[0]
    if host in ("viporder.com.vn", "www.viporder.com.vn"):
        return _fail([f"the base URL points at the MARKETING domain ({host}), not the provider"])

    password = os.environ["LIVE_REG_PASSWORD"]
    name = os.environ["LIVE_REG_NAME"]
    phone = os.environ["LIVE_REG_PHONE"]
    email = os.environ["LIVE_REG_EMAIL"]

    # VERIFY IN MEMORY BEFORE TRANSMITTING. Nothing is sent until every one of
    # these holds. No password value is ever printed — only whether it matches.
    expected_phone = os.environ.get("LIVE_REG_EXPECT_PHONE", "").strip()
    expected_email = os.environ.get("LIVE_REG_EXPECT_EMAIL", "").strip()
    problems: list[str] = []
    if not password:
        problems.append("the password is empty")
    if len(password) < 16:
        problems.append(f"the password is shorter than 16 characters (got {len(password)})")
    if expected_phone and phone != expected_phone:
        problems.append(f"phone is {phone!r}, expected {expected_phone!r}")
    if expected_email and email != expected_email:
        problems.append(f"email is {email!r}, expected {expected_email!r}")
    if name != name.strip() or not name:
        problems.append("name is empty or padded")

    # `confirmPassword` is built from `password`, so equality is by construction —
    # asserted anyway, because construction is not verification, and a later change
    # that lets the two diverge must fail here rather than at the provider.
    confirm_password = password
    if confirm_password != password:
        problems.append("confirmPassword does not equal password")
    accept_terms = True
    if accept_terms is not True:
        problems.append("acceptTerms is not exactly True")

    if problems:
        return _fail([f"pre-send check: {p}" for p in problems])

    body = {
        "name": name,
        "phone": phone,
        "email": email,
        "password": password,
        "confirmPassword": confirm_password,
        "acceptTerms": accept_terms,
    }
    # Exactly the provider's field names, in the provider's order, and nothing else.
    assert tuple(body) == PROVIDER_FIELDS, f"unexpected wire fields: {tuple(body)}"

    url = f"{base}/register"
    evidence_file = _evidence_path()
    print("LIVE REGISTRATION — ONE POST, NO RETRY")
    print(f"  endpoint   : {url}")
    print(f"  field names: {', '.join(body)}")
    print(f"  phone      : {body['phone']}")
    print(f"  email      : {body['email']}")
    print(f"  password   : <{len(password)} chars, never printed>")
    # PRINTED BEFORE THE POST ON PURPOSE: if the terminal dies, the operator still
    # knows where the record is. A path discovered only after the reply arrives is
    # worthless exactly when it is needed most.
    print(f"  evidence   : {evidence_file}")
    print("  (printed BEFORE the POST; the record is written whatever the answer)")
    print()

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - fixed https endpoint, operator-supplied
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "VIPORDER-integration-check/1.0 (+pre-staging verification)",
        },
        method="POST",
    )

    recorded_at = _now_iso()
    started = time.monotonic()
    status: int | None = None
    ctype: str | None = None
    raw = b""
    outcome = "http_response"
    note = ""
    redirect_refused = False
    redirect_location = "(none)"
    transport_failure: str | None = None

    try:
        opener = urllib.request.build_opener(_RefuseRedirects())
        with opener.open(req, timeout=30) as resp:  # noqa: S310
            status, ctype, raw = (
                resp.status,
                resp.headers.get("Content-Type", ""),
                resp.read(),
            )
    except urllib.error.HTTPError as exc:
        # A 30x lands here because `_RefuseRedirects` raised rather than followed it.
        # Say so plainly: an operator must understand this is a RESULT, and that
        # chasing it would spend a second POST the owner did not authorize.
        if 300 <= exc.code < 400:
            redirect_refused = True
            outcome = "redirect_refused"
            redirect_location = exc.headers.get("Location", "(none)")
            note = (
                f"the provider answered {exc.code}; Location={redirect_location}; "
                f"the redirect was NOT followed (one-POST budget)"
            )
        status, ctype, raw = exc.code, exc.headers.get("Content-Type", ""), exc.read()
    except urllib.error.URLError as exc:
        transport_failure = str(exc.reason)
        outcome = "transport_failure"
        note = f"transport failure: {exc.reason}"

    elapsed = time.monotonic() - started

    def record(**fields: object) -> None:
        report = _report(
            endpoint=url,
            recorded_at=recorded_at,
            outcome=outcome,
            note=note,
            **fields,  # type: ignore[arg-type]
        )
        try:
            _write_evidence(evidence_file, report, password)
            print(f"  evidence written: {evidence_file}")
        except OSError as exc:
            # Never lose the terminal report because the disk refused the file.
            print(f"  WARNING: evidence NOT written to {evidence_file}: {exc}", file=sys.stderr)

    if transport_failure is not None:
        print(f"  TRANSPORT FAILURE after {elapsed:.2f}s: {transport_failure}")
        print("\n  No response. This is NOT a contract answer — the request may or may")
        print("  not have reached the provider. Do NOT simply re-run: check with the")
        print("  provider whether the account was created before trying again.")
        record(
            elapsed_seconds=elapsed,
            status=None,
            content_type=None,
            raw_bytes=None,
            body_is_json=False,
            key_names=[],
            body=None,
            password_echoed=False,
        )
        return 1

    text = raw.decode("utf-8", errors="replace")
    password_echoed = bool(password) and _sanitize(text, password, truncate=False) != text
    if password_echoed:
        print("  NOTE: the response echoed the password; it is redacted below.")

    print(f"  HTTP status : {status}")
    print(f"  content-type: {ctype or '(none)'}")
    print(f"  elapsed     : {elapsed:.2f}s")
    print(f"  bytes       : {len(raw)}")

    if redirect_refused:
        print()
        print(f"  REDIRECT REFUSED: the provider answered {status}.")
        print(f"    Location: {redirect_location}")
        print("  The tool did NOT follow it. Following a redirect would be a SECOND")
        print("  HTTP request, and the authorization covers exactly one POST.")
        print("  Treat this as a consumed attempt and report it — do not retry.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print("\n  BODY IS NOT JSON. First 300 chars, redacted:")
        print("   ", _sanitize(text, password))
        print("\n  A non-JSON body means the contract question is still open.")
        record(
            elapsed_seconds=elapsed,
            status=status,
            content_type=ctype,
            raw_bytes=len(raw),
            body_is_json=False,
            key_names=[],
            body=_sanitize(text, password),
            password_echoed=password_echoed,
        )
        return 1

    key_names = _response_keys(parsed)
    print("\n  KEY NAMES (structure only):")
    for k in key_names[:40]:
        print(f"    {k}")

    print("\n  SANITIZED BODY:")
    print(json.dumps(_sanitize(parsed, password), indent=4, ensure_ascii=False)[:4000])

    record(
        elapsed_seconds=elapsed,
        status=status,
        content_type=ctype,
        raw_bytes=len(raw),
        body_is_json=True,
        key_names=key_names,
        body=_sanitize(parsed, password),
        password_echoed=password_echoed,
    )

    if redirect_refused:
        return 1
    if not (200 <= (status or 0) < 300):
        print("\n  The provider did not accept this registration. That is a RESULT:")
        print("  record it in docs/KHAIBAO9610-INTEGRATION.md rather than guessing.")
    return 0 if 200 <= (status or 0) < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
