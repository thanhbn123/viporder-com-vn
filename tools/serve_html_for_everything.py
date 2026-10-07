#!/usr/bin/env python3
"""NEGATIVE-CONTROL SERVER — answers 200 + index.html for EVERY path.

This is not a dev server and must never be deployed. It exists so the staging
asset check can be shown to FAIL when the site is broken, because a check that a
broken server also passes is not evidence.

WHY IT EXISTS. `tests/e2e-staging/staging.spec.js` used to assert only
`status === 200` and `body.length > 50` for robots.txt, sitemap.xml,
style.css and app.js. This server satisfies all four with the same HTML page —
so "robots, sitemap, CSS and JS are really served" PASSED against a host that
served none of them. §12.1 of the project's rules: the thing used to verify has
to be trustworthy itself.

HOW TO USE IT (from the repository root):

    python3 tools/serve_html_for_everything.py --port 8199 &
    STAGING_URL=http://127.0.0.1:8199 \\
      npx playwright test -c playwright.staging.config.js -g "robots, sitemap"

The fixed check fails against it; the old `status === 200` check passed against
it. Nothing outside 127.0.0.1 is bound, and no file outside the repository is
read.

`--spoof-types` is the second control: the same index.html body, but with the
Content-Type of a real .txt/.xml/.css/.js file. The content-type assertion passes
and only the MARKER assertion catches it — so both halves of the check are shown
to be load-bearing.
"""

from __future__ import annotations

import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"

#: What nginx would send for these, so the spoofed run looks like a real one.
SPOOFED_TYPES = {
    ".txt": "text/plain",
    ".xml": "text/xml",
    ".css": "text/css",
    ".js": "text/javascript",
}


class EverythingIsHtml(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    #: Set from `--spoof-types`.
    spoof_types = False

    def _content_type(self) -> str:
        if not self.spoof_types:
            return "text/html; charset=utf-8"
        path = self.path.split("?", 1)[0]
        # The UA may negotiate; ignore that so the control is deterministic.
        guessed = SPOOFED_TYPES.get(Path(path).suffix, None)
        if guessed is None:
            guessed = mimetypes.guess_type(path)[0] or "text/html"
        return guessed

    def _respond(self, *, with_body: bool) -> None:
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self._content_type())
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._respond(with_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._respond(with_body=False)

    def log_message(self, fmt: str, *args: object) -> None:
        print(
            f"  html-for-everything: {self.path} -> 200 {self._content_type()}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument(
        "--spoof-types",
        action="store_true",
        help="send the real mime type for the extension while still serving index.html",
    )
    args = parser.parse_args()
    if not PAGE.is_file():
        print(f"FAIL: {PAGE} not found — run this from the repository")
        return 1
    EverythingIsHtml.spoof_types = args.spoof_types
    with ThreadingHTTPServer(("127.0.0.1", args.port), EverythingIsHtml) as httpd:
        print(
            f"serving index.html for every path on http://127.0.0.1:{args.port}"
            f"{' (spoofing content-types)' if args.spoof_types else ''}",
            flush=True,
        )
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
