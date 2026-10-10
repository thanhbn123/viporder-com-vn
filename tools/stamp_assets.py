#!/usr/bin/env python3
"""Stamp every /static CSS/JS reference in the pages with a content hash.

WHY. nginx serves /static/* with `Cache-Control: public, max-age=2592000,
immutable` (deploy/nginx/viporder.com.vn.conf). With an unchanging URL a
browser that visited before keeps the OLD file for 30 days: measured
2026-10-10, the owner's browser did not show the new tracking rows although
production served the new tracking-search.js. A `?v=<hash of the file>` makes
the URL change exactly when the file does, so `immutable` stays correct.

    python tools/stamp_assets.py          # rewrite the pages in place
    python tools/stamp_assets.py --check  # exit 1 if any stamp is missing/stale

`tools/check_site.py` runs the check in CI, so a changed file with a stale
stamp cannot merge.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("index.html", "404.html")
REF = re.compile(r'((?:src|href)=")(/static/(?:css|js)/[^"?#]+)(\?v=[0-9a-f]*)?(")')


def file_hash(url_path: str) -> str:
    data = (ROOT / url_path.lstrip("/")).read_bytes()
    return hashlib.sha256(data).hexdigest()[:10]


def stamped(text: str) -> str:
    return REF.sub(
        lambda m: f"{m.group(1)}{m.group(2)}?v={file_hash(m.group(2))}{m.group(4)}", text
    )


def problems() -> list[str]:
    found = []
    for page in PAGES:
        text = (ROOT / page).read_text(encoding="utf-8")
        for m in REF.finditer(text):
            want = f"?v={file_hash(m.group(2))}"
            if m.group(3) != want:
                found.append(f"{page}: {m.group(2)}{m.group(3) or ''} should be {m.group(2)}{want}")
    return found


def main(argv: list[str]) -> int:
    if "--check" in argv:
        bad = problems()
        for line in bad:
            print(f"ERROR {line}  (run: python tools/stamp_assets.py)")
        return 1 if bad else 0
    for page in PAGES:
        path = ROOT / page
        text = path.read_text(encoding="utf-8")
        new = stamped(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(f"stamped {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
