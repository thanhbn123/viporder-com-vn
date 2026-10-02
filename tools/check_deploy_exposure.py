#!/usr/bin/env python3
"""Guard: what does the deploy actually PUBLISH to the web root?

WHY THIS EXISTS. The deploy copies the repository into nginx's document root and
then subtracts things it does not want to serve:

    rsync -a --delete --exclude '.git' --exclude 'backend' --exclude 'deploy' \\
      --exclude 'tools' --exclude 'docs' --exclude 'var' ./ /srv/viporder/site/

That is a DENY list, and deny lists rot in the dangerous direction: every new file
at the repository root is published **by default**. When the browser workstream
added `package.json`, `playwright.config.js`, `tests/` and `node_modules/`, all of
them became fetchable. Measured against a REAL nginx serving the real config:

    200  /package.json
    200  /package-lock.json          <- the whole dependency tree, for CVE lookup
    200  /playwright.config.js
    200  /tests/e2e/registration.spec.js
    200  /README.md
         node_modules/ copied in too

Nothing secret leaked: `backend/`, `deploy/`, `docs/` and `tools/` were already
excluded, and the dotfile rule denies `/.env` and `/.git`. But publishing the test
suite and a dependency tree from a marketing domain is not a defensible default,
and "we remembered to exclude it" is not a control.

The deploy now publishes an explicit ALLOW LIST (`deploy/published-files.txt`).
This tool checks that list, so it cannot rot the same way:

  1. every entry exists;
  2. every entry is something this project intends to publish;
  3. nothing sensitive is named;
  4. every LOCAL asset the pages reference is covered — a list that omits a file
     the site needs produces a broken site, which is the failure mode an allow
     list introduces and the reason check 4 exists.

It does NOT run rsync and does NOT validate nginx syntax.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "deploy" / "published-files.txt"

#: Top-level names this project publishes. Anything else is a finding.
PUBLISHED = {"index.html", "404.html", "robots.txt", "sitemap.xml", "static"}

#: Names that must never appear in the manifest, however they are spelled.
FORBIDDEN = {
    "backend", "deploy", "tools", "docs", "tests", "var", "node_modules",
    ".git", ".env", ".venv", "package.json", "package-lock.json",
    "playwright.config.js", "test-results", "playwright-report", "README.md",
}

#: Local URLs the pages reference, so the manifest can be checked for omissions.
#: Matches src="/..." and href="/..." that are not external.
ASSET_RE = re.compile(r'(?:src|href)="(/[^"]+)"')


def _entries() -> list[str]:
    if not MANIFEST.exists():
        print(f"FAIL: {MANIFEST} is missing — the deploy has no publish list")
        sys.exit(1)
    out = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.rstrip("/"))
    return out


def _local_assets() -> set[str]:
    """Every local path the shipped pages reference, as repo-relative names."""
    found: set[str] = set()
    for page in ("index.html", "404.html"):
        p = ROOT / page
        if not p.exists():
            continue
        for url in ASSET_RE.findall(p.read_text(encoding="utf-8")):
            if url.startswith("//") or url.startswith("/api/"):
                continue
            found.add(url.lstrip("/"))
    return found


def main() -> int:
    errors: list[str] = []
    notes: list[str] = []

    entries = _entries()
    if not entries:
        errors.append("the publish list is empty — the deploy would ship nothing")
    if "index.html" not in entries:
        errors.append("index.html is not published — the deploy would ship no homepage")

    for e in entries:
        top = e.split("/")[0]
        if top in FORBIDDEN:
            errors.append(
                f"'{e}' is in the publish list but must never be published "
                f"(top-level '{top}')"
            )
        elif top not in PUBLISHED:
            errors.append(
                f"'{e}' is in the publish list but is not part of the public site. "
                f"Publishing is now explicit; add it to PUBLISHED in this tool and "
                f"to the manifest only if it is genuinely public."
            )
        elif not (ROOT / e).exists():
            errors.append(f"'{e}' is in the publish list but does not exist")

    # Every local asset the pages reference must be covered by an entry.
    covered = set()
    for e in entries:
        covered.add(e)
    for asset in _local_assets():
        if asset in covered:
            continue
        if any(asset == c or asset.startswith(c + "/") for c in covered):
            continue
        errors.append(
            f"the pages reference /{asset} but the publish list does not cover it — "
            f"the deployed site would 404 on a file it needs"
        )

    print(f"  publish list: {', '.join(entries) or '(empty)'}")
    for a in sorted(_local_assets()):
        notes.append(f"referenced asset /{a}")
    for n in notes[:6]:
        print(f"  {n}")
    if len(notes) > 6:
        print(f"  ... and {len(notes) - 6} more referenced assets")
    for e in errors:
        print(f"ERROR {e}")

    print(f"deploy-exposure: {len(entries)} entr(ies), {len(errors)} error(s)")
    print(
        "scope: the rsync publish list in deploy/published-files.txt against the "
        "repository tree and the pages' own local asset references — this does NOT "
        "run rsync, does NOT read the docs' shell commands, and does NOT validate "
        "nginx syntax (use `nginx -t`)."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
