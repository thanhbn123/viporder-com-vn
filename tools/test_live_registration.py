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

NEVER PRINTS THE PASSWORD. Not on success, not on failure, not inside an error
body — the response is scanned and any occurrence of the password is redacted
before anything is written to the terminal.

USAGE (the owner runs this; substitute real values):

    ENABLE_LIVE_REGISTRATION_TEST=yes \\
    LIVE_REG_NAME='Khách Kiểm Thử' \\
    LIVE_REG_PHONE='0900000000' \\
    LIVE_REG_EMAIL='test@example.com' \\
    LIVE_REG_PASSWORD='<a-real-test-password>' \\
    python3 tools/test_live_registration.py

Add `--base-url https://apiviporder.com/frontend/v1` to override the endpoint.

WHAT IT PRINTS: HTTP status, content type, elapsed time, the response's KEY NAMES
(never values blindly), and a sanitized body with any password occurrence redacted
and long values truncated. That is enough to write a real contract from, and not
enough to leak a credential into a terminal scrollback or a CI log.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

ENABLE_FLAG = "ENABLE_LIVE_REGISTRATION_TEST"
DEFAULT_BASE = "https://apiviporder.com/frontend/v1"

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


def _sanitize(value: object, secret: str, depth: int = 0) -> object:
    """Redact the password and truncate long values, recursively."""
    if depth > 6:
        return "<max depth>"
    if isinstance(value, dict):
        return {k: _sanitize(v, secret, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, secret, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        out = value.replace(secret, "<REDACTED>") if secret else value
        return out if len(out) <= MAX_VALUE else out[:MAX_VALUE] + "...<truncated>"
    return value


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
        return _fail(
            [f"the base URL points at the MARKETING domain ({host}), not the provider"]
        )

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
        problems.append(
            f"the password is shorter than 16 characters (got {len(password)})"
        )
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
    print("LIVE REGISTRATION — ONE POST, NO RETRY")
    print(f"  endpoint   : {url}")
    print(f"  field names: {', '.join(body)}")
    print(f"  phone      : {body['phone']}")
    print(f"  email      : {body['email']}")
    print(f"  password   : <{len(password)} chars, never printed>")
    print()

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(  # fixed https endpoint, operator-supplied
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "VIPORDER-integration-check/1.0 (+pre-staging verification)",
        },
        method="POST",
    )

    started = time.monotonic()
    try:
        opener = urllib.request.build_opener(_RefuseRedirects())
        with opener.open(req, timeout=30) as resp:
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
            print(f"  REDIRECT REFUSED: the provider answered {exc.code}.")
            print(f"    Location: {exc.headers.get('Location', '(none)')}")
            print()
            print(
                "  The tool did NOT follow it. Following a redirect would be a SECOND"
            )
            print("  HTTP request, and the authorization covers exactly one POST.")
            print("  Treat this as a consumed attempt and report it — do not retry.")
            print("  The budget is spent: one POST was transmitted and answered 30x.")
            return 1
        status, ctype, raw = exc.code, exc.headers.get("Content-Type", ""), exc.read()
    except urllib.error.URLError as exc:
        print(
            f"  TRANSPORT FAILURE after {time.monotonic() - started:.2f}s: {exc.reason}"
        )
        print("\n  No response. This is NOT a contract answer — the request may or may")
        print("  not have reached the provider. Do NOT simply re-run: check with the")
        print("  provider whether the account was created before trying again.")
        return 1

    elapsed = time.monotonic() - started
    text = raw.decode("utf-8", errors="replace")
    if password and password in text:
        print("  NOTE: the response echoed the password; it is redacted below.")

    print(f"  HTTP status : {status}")
    print(f"  content-type: {ctype or '(none)'}")
    print(f"  elapsed     : {elapsed:.2f}s")
    print(f"  bytes       : {len(raw)}")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print("\n  BODY IS NOT JSON. First 300 chars, redacted:")
        print("   ", _sanitize(text, password))
        print("\n  A non-JSON body means the contract question is still open.")
        return 1

    def keys(o: object, prefix: str = "", depth: int = 0) -> list[str]:
        if depth > 4:
            return []
        if isinstance(o, dict):
            out = []
            for k, v in o.items():
                out.append(f"{prefix}{k}")
                out.extend(keys(v, f"{prefix}{k}.", depth + 1))
            return out
        if isinstance(o, list) and o:
            return keys(o[0], f"{prefix}[].", depth + 1)
        return []

    print("\n  KEY NAMES (structure only):")
    for k in keys(parsed)[:40]:
        print(f"    {k}")

    print("\n  SANITIZED BODY:")
    print(json.dumps(_sanitize(parsed, password), indent=4, ensure_ascii=False)[:4000])

    if not (200 <= status < 300):
        print("\n  The provider did not accept this registration. That is a RESULT:")
        print("  record it in docs/KHAIBAO9610-INTEGRATION.md rather than guessing.")
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
