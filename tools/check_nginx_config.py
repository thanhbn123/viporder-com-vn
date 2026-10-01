#!/usr/bin/env python3
"""Guard against the nginx `add_header` inheritance trap.

Nginx does NOT merge `add_header` across configuration levels:

    "These directives are inherited from the previous configuration level if
     and only if there are no add_header directives defined on the current
     level."

So a single `add_header Cache-Control ...` inside a `location` silently cancels
EVERY server-level `add_header` for that location. In this project that meant
the homepage — served by `location = /`, which needs its own Cache-Control —
would have shipped with no CSP, no HSTS, no X-Frame-Options, no
X-Content-Type-Options, no Referrer-Policy and no Permissions-Policy, while a
spot check on any other path looked perfect.

That bug was shipped once and caught only by an adversarial reviewer. A comment
would not have stopped it, so this check exists instead: any `location` block
that declares its own `add_header` must also include the security-headers
snippet.

Run:  python3 tools/check_nginx_config.py
Exit 0 = every location that needs the include has it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX_DIR = ROOT / "deploy" / "nginx"
SITE_CONF = NGINX_DIR / "viporder.com.vn.conf"
SNIPPET_NAME = "viporder-security-headers.conf"

SECURITY_HEADERS = (
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "content-security-policy",
)


def strip_comments(line: str) -> str:
    """Remove a trailing `#` comment, ignoring `#` inside quotes."""
    out, quote = [], None
    for ch in line:
        if quote:
            if ch == quote:
                quote = None
            out.append(ch)
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def blocks(text: str) -> list[tuple[str, str, int]]:
    """Return (kind, body, start_line) for each top-level `location` block."""
    found: list[tuple[str, str, int]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = strip_comments(lines[i]).strip()
        if re.match(r"^location\b.*\{", stripped):
            kind = stripped.split("{")[0].strip()
            depth = stripped.count("{") - stripped.count("}")
            body: list[str] = []
            start = i + 1
            i += 1
            while i < len(lines) and depth > 0:
                cleaned = strip_comments(lines[i])
                depth += cleaned.count("{") - cleaned.count("}")
                if depth > 0:
                    body.append(cleaned)
                i += 1
            found.append((kind, "\n".join(body), start))
        else:
            i += 1
    return found


def main() -> int:
    errors: list[str] = []
    notes: list[str] = []

    if not SITE_CONF.is_file():
        print(f"FAIL: {SITE_CONF.relative_to(ROOT)} not found")
        return 1

    snippet = NGINX_DIR / SNIPPET_NAME
    if not snippet.is_file():
        print(f"FAIL: {snippet.relative_to(ROOT)} not found")
        return 1

    text = SITE_CONF.read_text(encoding="utf-8")

    # 1. The snippet must actually define every header we claim to send.
    snippet_text = snippet.read_text(encoding="utf-8").lower()
    for header in SECURITY_HEADERS:
        if header not in snippet_text:
            errors.append(f"{SNIPPET_NAME}: does not define {header}")

    # 2. Any location declaring its own add_header must include the snippet.
    #    No location may set a security header directly — that is how the two
    #    copies drift apart.
    locs = blocks(text)
    if not locs:
        errors.append(f"{SITE_CONF.name}: no location blocks found — parser broken?")

    for kind, body, line in locs:
        low = body.lower()
        own_headers = re.findall(r"^\s*add_header\s+([A-Za-z-]+)", body, re.MULTILINE)
        if not own_headers:
            notes.append(f"line {line}: {kind} — no add_header, inherits (ok)")
            continue

        for header in own_headers:
            if header.lower() in SECURITY_HEADERS:
                errors.append(
                    f"{SITE_CONF.name}:{line}: {kind} sets {header} directly; "
                    f"security headers must come from the shared snippet only"
                )

        if SNIPPET_NAME not in body:
            errors.append(
                f"{SITE_CONF.name}:{line}: {kind} declares add_header "
                f"({', '.join(own_headers)}) but does NOT include {SNIPPET_NAME} — "
                f"nginx will drop ALL server-level security headers for this location"
            )
        else:
            notes.append(
                f"line {line}: {kind} — declares {', '.join(own_headers)} "
                f"and includes the snippet (ok)"
            )
        _ = low

    # 3. The server block itself must include the snippet, for locations that
    #    do not override anything.
    if SNIPPET_NAME not in text:
        errors.append(f"{SITE_CONF.name}: server level never includes {SNIPPET_NAME}")

    for note in notes:
        print(f"  {note}")
    for err in errors:
        print(f"ERROR {err}")

    print()
    print(f"nginx-config: {len(locs)} location block(s), "
          f"{len(errors)} error(s)")
    print("scope: add_header/include placement in deploy/nginx/*.conf only — "
          "this does NOT validate nginx syntax; run `nginx -t` for that.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
