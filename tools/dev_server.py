#!/usr/bin/env python3
"""Local development server for the VIPORDER.COM.VN static site.

Serves the repository root as static files and reverse-proxies `/api/*` to the
backend so the browser sees a single origin — exactly like production behind
Nginx. This matters: without it, the front end would need CORS, and CORS is
deliberately disabled in the application.

Usage:
    # terminal 1
    cd backend && uvicorn app.main:app --port 8000
    # terminal 2
    python3 tools/dev_server.py            # http://127.0.0.1:8080
    python3 tools/dev_server.py --port 9000 --api http://127.0.0.1:8000

Development only. Never run this in production.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


class DevHandler(SimpleHTTPRequestHandler):
    api_base = "http://127.0.0.1:8000"
    proxy_timeout = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # -- logging ------------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- routing ------------------------------------------------------------
    def _is_api(self) -> bool:
        return self.path == "/api" or self.path.startswith("/api/")

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        target = self.api_base.rstrip("/") + self.path
        req = urllib.request.Request(target, data=body, method=method)
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            req.add_header(key, value)

        try:
            with urllib.request.urlopen(req, timeout=self.proxy_timeout) as resp:
                payload = resp.read()
                status = resp.status
                headers = resp.headers
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            headers = exc.headers
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # The front end must see a real failure, not an empty 200.
            message = (
                '{"error":{"code":"BACKEND_UNREACHABLE",'
                f'"message":"Dev proxy could not reach the backend at {self.api_base}: {exc}"'
                "}}"
            ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return

        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - stdlib signature
        if self._is_api():
            return self._proxy("GET")
        return super().do_GET()

    def send_error(self, code, message=None, explain=None):  # type: ignore[override]
        """Serve our own 404 page, the way nginx does.

        WHY THIS EXISTS. This server's whole purpose is to make the browser see
        what production sees (see the module docstring). Production runs nginx
        with `error_page 404 /404.html` (`deploy/nginx/viporder.com.vn.conf:138`),
        so a mistyped URL gets OUR branded error page. This server delegated to
        `SimpleHTTPRequestHandler`, which answers with Python's built-in error
        page instead — so the 404 behaviour was the one page that could never be
        reviewed locally, and a browser test asserting `noindex` on it failed
        against a page no customer will ever be served.

        `404.html` carries `noindex` and deliberately no canonical; serving it
        with a real 404 status is what keeps those controls meaningful. The status
        code is preserved — this is not a soft-404.
        """
        if code == 404:
            page = ROOT / "404.html"
            if page.is_file():
                body = page.read_bytes()
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
        return super().send_error(code, message, explain)

    def do_HEAD(self):  # noqa: N802
        if self._is_api():
            return self._proxy("HEAD")
        return super().do_HEAD()

    def do_POST(self):  # noqa: N802
        if self._is_api():
            return self._proxy("POST")
        self.send_error(405, "POST is only proxied under /api")

    def do_PUT(self):  # noqa: N802
        if self._is_api():
            return self._proxy("PUT")
        self.send_error(405)

    def do_DELETE(self):  # noqa: N802
        if self._is_api():
            return self._proxy("DELETE")
        self.send_error(405)

    # -- caching ------------------------------------------------------------
    def end_headers(self):
        # Development: never let the browser cache, so a reload always shows
        # the file that is actually on disk.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument(
        "--api",
        default="http://127.0.0.1:8000",
        help="backend base URL to proxy /api to",
    )
    args = parser.parse_args()

    DevHandler.api_base = args.api
    handler = partial(DevHandler)

    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    print("VIPORDER dev server")
    print(f"  site  : http://{args.bind}:{args.port}/   (root: {ROOT})")
    print(f"  /api  : -> {args.api}")
    print("  Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
