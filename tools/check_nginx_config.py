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


def strip_comments_block(text: str) -> str:
    """strip_comments() applied line by line.

    `strip_comments` takes a SINGLE line. Calling it with a whole file makes it
    treat the file as one line and delete everything from the first `#` — which
    silently emptied the text this guard was searching, so the guard both missed
    the real defect and reported two false ones.
    """
    return "\n".join(strip_comments(line) for line in text.split("\n"))


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

    # 4. An API proxied through nginx must NOT have its error bodies intercepted.
    #
    #    `error_page 404 /404.html;` is declared at SERVER level in the site config,
    #    so it applies to the `/api/` locations too. Combined with
    #    `proxy_intercept_errors on` in the shared proxy params, nginx replaced the
    #    application's own JSON 404 with the marketing page — MEASURED on staging at
    #    36caed7:
    #
    #      app   ->404 application/json  {"error":{"code":"NOT_FOUND", ...}}
    #      nginx ->404 text/html         the branded 404 page
    #
    #    `static/js/tracking.js` parses that body as JSON, so a customer who mistyped
    #    a tracking code saw a generic failure instead of the one message that tells
    #    them what to do.
    #
    #    The check is deliberately narrow: this params file is included by the API
    #    locations and nothing else (asserted below), so "the value must be off" is
    #    the whole rule. A first attempt tried to walk braces to decide whether the
    #    `error_page` was server-level; it never fired, and a guard that cannot fail
    #    is worse than none because it reads as protection.
    params = NGINX_DIR / "proxy_params_viporder"
    if not params.exists():
        errors.append(
            f"{params.name}: missing — the API proxy params are not in the tree"
        )
    else:
        ptxt = strip_comments_block(params.read_text(encoding="utf-8"))
        intercept = [
            ln.strip()
            for ln in ptxt.split("\n")
            if ln.strip().startswith("proxy_intercept_errors")
        ]
        if not intercept:
            errors.append(
                f"{params.name}: does not set `proxy_intercept_errors`. The nginx "
                f"default is `off`, but stating it explicitly is what this guard "
                f"checks — silence here is how the defect came back once already."
            )
        elif intercept[-1].endswith("on;"):
            errors.append(
                f"{params.name}: `{intercept[-1]}` — with the server-level "
                f"`error_page` in {SITE_CONF.name}, nginx replaces the application's "
                f"JSON error bodies with the HTML page, and the tracking UI parses "
                f"those bodies as JSON. It must be `off;`."
            )
        else:
            notes.append(
                f"{params.name}: {intercept[-1]} — API error bodies pass through (ok)"
            )

        # The rule above is only sound while the file is API-only.
        includers = [
            f.name
            for f in NGINX_DIR.glob("*.conf")
            if f"include /etc/nginx/{params.name}"
            in strip_comments_block(f.read_text(encoding="utf-8"))
        ]
        if not includers:
            errors.append(
                f"{params.name}: nothing includes it — the API locations are not "
                f"using the shared proxy params"
            )

        # -- 5. ONE SOURCE PER SECURITY HEADER ON THE PROXY PATH -------------
        #
        # nginx `add_header` ADDS to a proxied response, and the application sets
        # its own copies of these headers, so the two tiers STACK. MEASURED on
        # /api/v1/health with `curl -D-` before this check existed:
        #
        #   x-content-type-options x2 · x-frame-options x2 · referrer-policy x2
        #   permissions-policy x2 · content-security-policy x2 (two DIFFERENT
        #   policies) · strict-transport-security x2
        #
        # Duplicate CSP is the worst of them, because browsers intersect policies:
        # the effective /api/* policy was the intersection of the application's
        # strict one and nginx's marketing one, i.e. a policy nobody chose.
        #
        # The rule: every security header the snippet defines must be HIDDEN from
        # the upstream on the proxy path, so the snippet is the single source. And
        # the hide list must not name anything the snippet does not define —
        # hiding a header with no other source DELETES it.
        snippet_headers = {
            h.lower()
            for h in re.findall(
                r"^\s*add_header\s+([A-Za-z-]+)",
                strip_comments_block(snippet.read_text(encoding="utf-8")),
                re.MULTILINE,
            )
        }
        hidden = {
            h.lower()
            for h in re.findall(
                r"^\s*proxy_hide_header\s+([A-Za-z-]+)", ptxt, re.MULTILINE
            )
        }
        # Content-Security-Policy IS A DELIBERATE EXCEPTION, and it is the one header
        # where de-duplicating makes things WORSE.
        #
        # The other five carry the SAME value from both tiers, so one copy is right.
        # CSP differs: nginx sends the MARKETING policy (which needs 'unsafe-inline'
        # and the analytics hosts for the static site) and the application sends a
        # STRICT one. Browsers INTERSECT multiple CSPs, so both together give the
        # STRICTER of the two. Hiding the application's copy leaves /api/* with only
        # the marketing policy — a WEAKER effective policy than before the
        # de-duplication work. That regression was measured on staging.
        #
        # So CSP is expected to appear in BOTH sets, and this guard asserts exactly
        # that rather than tolerating it silently: if someone hides CSP again, or
        # stops nginx sending it, this fails.
        CSP = "content-security-policy"
        stacked = sorted((snippet_headers - hidden) - {CSP})
        if stacked:
            errors.append(
                f"{params.name}: {', '.join(stacked)} — the application sends its own "
                f"copy and nginx `add_header` ADDS, so /api/* returns TWO of each. "
                f"Add `proxy_hide_header` for every one of them."
            )
        elif CSP not in snippet_headers:
            errors.append(
                f"{snippet.name}: no Content-Security-Policy — the marketing policy is "
                f"the only one that may stack, and it must exist"
            )
        else:
            notes.append(
                f"{params.name}: hides {len(hidden)} of {len(snippet_headers)} header(s) "
                f"the snippet defines; Content-Security-Policy deliberately NOT hidden so "
                f"the stricter app policy intersects the marketing one (ok)"
            )
        if CSP in hidden:
            errors.append(
                f"{params.name}: Content-Security-Policy IS hidden. That removes the "
                f"application's STRICT policy and leaves /api/* with only nginx's "
                f"marketing policy ('unsafe-inline' + third parties) — browsers "
                f"intersect CSPs, so hiding the stricter one WEAKENS the effective "
                f"policy. Leave both."
            )
        over_hidden = sorted(hidden - snippet_headers)
        if over_hidden:
            errors.append(
                f"{params.name}: proxy_hide_header {', '.join(over_hidden)} — the security "
                f"snippet does not define this, so hiding it removes the ONLY source of "
                f"the header rather than de-duplicating it"
            )

        # -- 6. every proxying location actually uses these params ------------
        #     `proxy_hide_header` and `proxy_intercept_errors` only apply where
        #     this file is included, so a new /api location without the include
        #     silently reintroduces both defects.
        proxying = [
            (kind, line) for kind, body, line in locs if "proxy_pass" in body.lower()
        ]
        if not proxying:
            errors.append(
                f"{SITE_CONF.name}: no location proxies — parser broken, or the API "
                f"locations were removed"
            )
        for kind, line in proxying:
            body = next(b for k, b, ln in locs if ln == line and k == kind)
            if f"include /etc/nginx/{params.name}" not in body:
                errors.append(
                    f"{SITE_CONF.name}:{line}: {kind} proxies but does NOT include "
                    f"{params.name} — duplicate security headers and intercepted error "
                    f"bodies both come back for this location"
                )
            else:
                notes.append(
                    f"line {line}: {kind} — proxies and includes {params.name} (ok)"
                )

    for note in notes:
        print(f"  {note}")
    for err in errors:
        print(f"ERROR {err}")

    print()
    print(f"nginx-config: {len(locs)} location block(s), {len(errors)} error(s)")
    print(
        "scope: add_header/include placement in deploy/nginx/*.conf only — "
        "this does NOT validate nginx syntax; run `nginx -t` for that."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
