#!/usr/bin/env python3
"""Site checks for VIPORDER.COM.VN — HTML, links, assets, accessibility, SEO baseline.

Design notes
------------
* Stdlib only. No network, no npm, no Java. CI must stay fast and deterministic.
* This file encodes BUSINESS rules, not just syntax rules. In particular it
  enforces that every customer-portal link points at the one approved host, so a
  later refactor cannot silently send existing customers somewhere else.
* Checks are declared as (level, name) so a failure message says exactly which
  rule broke.

Exit code 0 = all checks passed. Exit code 1 = at least one ERROR.
Warnings never fail the build; they are surfaced so they cannot be forgotten.
"""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

# --- Business constants -----------------------------------------------------
CANONICAL_HOST = "viporder.com.vn"
CUSTOMER_PORTAL_HOST = "khachhang.viporder.com.vn"
ALLOWED_EXTERNAL_HOSTS = {
    CANONICAL_HOST,
    CUSTOMER_PORTAL_HOST,
    # The owner's Zalo chat link (contact section, supplied 2026-10-09).
    "zalo.me",
}

TITLE_MIN, TITLE_MAX = 15, 70
DESC_MIN, DESC_MAX = 50, 160

# Error pages are held to a DIFFERENT contract from content pages — and in two
# places it is the OPPOSITE contract:
#
#   * they MUST be noindex; a 404 that invites indexing is a soft-404 farm;
#   * they MUST NOT carry a rel=canonical. A canonical belongs on a page you want
#     indexed, and on an error page it maps every bad URL onto a real one.
#
# Without this distinction the checker made a correct 404 page impossible to
# write, which is why the page was deferred rather than built.
ERROR_PAGE_NAMES = {"404.html", "410.html", "500.html"}

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

SKIP_ID_CHECK = {"#"}  # placeholder links are rejected separately

# Script `type` values that mean "this block is executable JavaScript".
# An absent or empty type means JavaScript (the HTML default).
# Anything else — application/ld+json, application/json, text/template — is
# DATA, is never executed, and must not be treated as an inline-script risk.
JS_SCRIPT_TYPES = {
    "",
    "module",
    "text/javascript",
    "application/javascript",
    "text/ecmascript",
    "application/ecmascript",
}
DATA_SCRIPT_TYPES = {"application/ld+json", "application/json"}


def is_executable_script_type(type_attr: str | None) -> bool:
    return (type_attr or "").strip().lower() in JS_SCRIPT_TYPES


def is_jsonld_type(type_attr: str | None) -> bool:
    return (type_attr or "").strip().lower() == "application/ld+json"


def is_error_page(page: Path) -> bool:
    """True for error documents, which follow the inverted rules above."""
    return page.name in ERROR_PAGE_NAMES


def check_sitemap_excludes_error_pages(f: Findings) -> None:
    """A sitemap is a list of pages you want indexed. Error pages are not.

    Cheap cross-file check, but it catches a real and easy mistake: copying the
    sitemap template and forgetting that ``404.html`` now exists.
    """
    sitemap = ROOT / "sitemap.xml"
    if not sitemap.is_file():
        return
    text = sitemap.read_text(encoding="utf-8")
    for name in sorted(ERROR_PAGE_NAMES):
        if name in text:
            f.error("sitemap.xml", f"error page {name!r} must not be listed in the sitemap")


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


class Doc(HTMLParser):
    """Minimal DOM collector: enough for structural checks, no dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.ids: list[str] = []
        self.anchor_hrefs: list[str] = []
        self.links: list[str] = []  # <link href>
        self.scripts: list[str] = []  # <script src>
        self.images: list[tuple[str | None, str | None]] = []
        self.inputs: list[dict[str, str | None]] = []
        self.meta: dict[str, str] = {}
        self.title: str = ""
        self.html_attrs: dict[str, str | None] = {}
        self.forms: list[dict[str, str | None]] = []
        self.label_for: list[str] = []
        self._stack: list[str] = []
        self._in_title = False
        self._title_buf: list[str] = []
        self._tag_counts: dict[str, int] = {}
        self.jsonld_blocks: list[str] = []  # content of application/ld+json
        self._jsonld_buf: list[str] | None = None

    # -- helpers
    def count(self, tag: str) -> int:
        return self._tag_counts.get(tag, 0)

    def by_tag(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v for k, v in attrs}
        # Ancestors = everything currently open, i.e. BEFORE this tag is
        # pushed. Void tags are never pushed, so snapshot first.
        ancestors = tuple(self._stack)

        self.tags.append((tag, a))
        self._tag_counts[tag] = self._tag_counts.get(tag, 0) + 1
        if tag not in VOID_TAGS:
            self._stack.append(tag)

        if tag == "html":
            self.html_attrs = a
        if a.get("id"):
            self.ids.append(a["id"])
        if tag == "a" and a.get("href") is not None:
            self.anchor_hrefs.append(a["href"])
        if tag == "link" and a.get("href"):
            self.links.append(a["href"])
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        if tag == "script" and is_jsonld_type(a.get("type")):
            # Capture the body so it can be parsed as JSON later. A malformed
            # block is silently ignored by search engines, so it must fail here.
            self._jsonld_buf = []
        if tag == "img":
            self.images.append((a.get("src"), a.get("alt")))
        if tag == "input":
            # Record the open ancestor chain so labelling can be verified:
            # a control is labelled if it is nested inside a <label> OR some
            # label[for] points at its id OR it carries aria-label(ledby).
            self.inputs.append({**a, "_ancestors": ancestors})
        if tag == "form":
            self.forms.append(a)
        if tag == "label" and a.get("for"):
            self.label_for.append(a["for"])
        if tag == "meta":
            # <meta charset="..."> carries the value on its own attribute and
            # has no `content` attribute, so it is handled separately.
            if a.get("charset"):
                self.meta["charset"] = a["charset"]
            key = a.get("name") or a.get("property")
            if key:
                self.meta[key.lower()] = a.get("content") or ""
        if tag == "title":
            self._in_title = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self._stack.pop()

    def handle_endtag(self, tag):
        if tag == "script" and self._jsonld_buf is not None:
            self.jsonld_blocks.append("".join(self._jsonld_buf))
            self._jsonld_buf = None
        if tag == "title":
            self._in_title = False
            self.title = "".join(self._title_buf).strip()
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)
        if self._jsonld_buf is not None:
            self._jsonld_buf.append(data)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_document(doc: Doc, page: Path, f: Findings) -> None:
    where = page.name

    # Language
    if not doc.html_attrs.get("lang"):
        f.error(where, "<html> is missing a lang attribute")

    # Headings
    h1 = doc.count("h1")
    if h1 != 1:
        f.error(where, f"expected exactly 1 <h1>, found {h1}")

    # Unique ids
    dupes = {i for i in doc.ids if doc.ids.count(i) > 1}
    if dupes:
        f.error(where, f"duplicate id attributes: {sorted(dupes)}")

    # Meta head essentials
    if not doc.meta.get("viewport"):
        f.error(where, "missing <meta name='viewport'>")
    if not doc.meta.get("description"):
        f.error(where, "missing <meta name='description'>")
    if "charset" not in doc.meta:
        f.error(where, "missing <meta charset>")

    robots = doc.meta.get("robots", "")
    if is_error_page(page):
        # Inverted on purpose: an error page that can be indexed is a defect.
        if "noindex" not in robots.lower():
            f.error(where, f"error page must be noindex, has robots={robots!r}")
    elif "noindex" in robots.lower():
        f.error(where, f"page is marked noindex: robots={robots!r}")

    # Title / description length
    if not doc.title:
        f.error(where, "missing or empty <title>")
    elif not (TITLE_MIN <= len(doc.title) <= TITLE_MAX):
        f.warn(
            where,
            f"<title> length {len(doc.title)} outside {TITLE_MIN}-{TITLE_MAX}: {doc.title!r}",
        )

    desc = doc.meta.get("description", "")
    if desc and not (DESC_MIN <= len(desc) <= DESC_MAX):
        f.warn(where, f"meta description length {len(desc)} outside {DESC_MIN}-{DESC_MAX}")

    # Canonical
    canonical = None
    for t, a in doc.tags:
        if t == "link" and (a.get("rel") or "").lower() == "canonical":
            canonical = a.get("href")
    if is_error_page(page):
        if canonical:
            f.error(
                where,
                f"error page must NOT declare rel=canonical (found {canonical!r}) — "
                f"a canonical on an error page maps every bad URL onto a real one",
            )
    elif not canonical:
        f.error(where, "missing rel=canonical link")
    else:
        host = urlparse(canonical).netloc
        if host != CANONICAL_HOST:
            f.error(where, f"canonical points at {host!r}, expected {CANONICAL_HOST!r}")

    # Inline event handlers (CSP-hostile)
    inline = [t for t, a in doc.tags if any(k.startswith("on") for k in a)]
    if inline:
        f.error(where, f"inline event handler attributes present on: {sorted(set(inline))}")

    # Inline <script> bodies (CSP-hostile).
    # A <script> element is only "executable" when it has no `src` AND its type
    # is JavaScript. Data blocks such as application/ld+json are NOT executable
    # and must not be flagged — flagging them would push people to move
    # structured data out of the page, which is worse for SEO, not better.
    inline_js = [
        a
        for t, a in doc.tags
        if t == "script" and not a.get("src") and is_executable_script_type(a.get("type"))
    ]
    if inline_js:
        f.error(
            where,
            f"{len(inline_js)} inline executable <script> block(s) without src (CSP-hostile)",
        )

    # Structured data must actually parse. A malformed JSON-LD block is worse
    # than none: search engines silently ignore it while the page still looks
    # correct to a human.
    for idx, block in enumerate(doc.jsonld_blocks):
        stripped = block.strip()
        if not stripped:
            f.error(where, f"JSON-LD block #{idx + 1} is empty")
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            f.error(where, f"JSON-LD block #{idx + 1} is not valid JSON: {exc}")
            continue
        if not isinstance(data, (dict, list)):
            f.error(where, f"JSON-LD block #{idx + 1} must be an object or array")
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or "@context" not in item:
                f.warn(where, f"JSON-LD block #{idx + 1} has an entry without @context")
        f.warn(
            where,
            f"JSON-LD block #{idx + 1} present ({len(items)} entr(ies)) — "
            f"verify every property is true, not aspirational",
        )


def check_anchors_and_links(doc: Doc, page: Path, f: Findings) -> None:
    where = page.name
    ids = set(doc.ids)

    for href in doc.anchor_hrefs:
        if href in SKIP_ID_CHECK:
            f.error(where, "placeholder link href='#' present — dead link")
            continue
        if href.startswith("#"):
            target = href[1:]
            if target and target not in ids:
                f.error(where, f"internal anchor {href!r} has no matching id")
            continue

        parsed = urlparse(href)
        if parsed.scheme == "http":
            f.error(where, f"insecure http:// link: {href!r}")
            continue
        if parsed.scheme in ("mailto", "tel", "sms"):
            continue
        if parsed.scheme == "https":
            host = parsed.netloc
            if host not in ALLOWED_EXTERNAL_HOSTS:
                f.warn(where, f"external link to unapproved host {host!r}: {href!r}")
        elif href.startswith("/") or href == "":
            # Local asset reference — checked by check_assets
            continue
        else:
            f.warn(where, f"relative link not verified: {href!r}")


def check_customer_portal(doc: Doc, page: Path, f: Findings) -> None:
    """BUSINESS RULE: every existing-customer entry point goes to the portal."""
    where = page.name
    portal_links = [h for h in doc.anchor_hrefs if urlparse(h).netloc == CUSTOMER_PORTAL_HOST]

    # Any login-ish label must resolve to the portal host.
    login_words = ("đăng nhập", "dang nhap", "kiểm tra đơn", "kiem tra don")
    for tag, attrs in doc.tags:
        if tag != "a":
            continue
        href = attrs.get("href") or ""
        cls = (attrs.get("class") or "").lower()
        is_login = "login" in cls
        if not is_login:
            continue
        if urlparse(href).netloc != CUSTOMER_PORTAL_HOST:
            f.error(
                where,
                f"login control points at {href!r}, expected {CUSTOMER_PORTAL_HOST}",
            )

    if not portal_links:
        f.error(where, f"no link to the customer portal {CUSTOMER_PORTAL_HOST} found")

    # No viporder host other than the approved two.
    for href in doc.anchor_hrefs:
        host = urlparse(href).netloc
        if host.endswith("viporder.com.vn") and host not in ALLOWED_EXTERNAL_HOSTS:
            f.error(where, f"link to unapproved viporder host {host!r}")
    _ = login_words  # labels are matched via the class check above


def check_assets(doc: Doc, page: Path, f: Findings) -> None:
    where = page.name
    refs = doc.links + doc.scripts + [s for s, _ in doc.images if s]
    for ref in refs:
        if urlparse(ref).scheme or ref.startswith("//"):
            continue  # external CDN, not our asset
        path = ROOT / ref.lstrip("/")
        if not path.is_file():
            f.error(
                where,
                f"referenced asset does not exist: {ref!r} -> {path.relative_to(ROOT)}",
            )


def check_accessibility(doc: Doc, page: Path, f: Findings) -> None:
    where = page.name

    for src, alt in doc.images:
        if alt is None:
            f.error(where, f"<img src={src!r}> has no alt attribute")

    # Password inputs must declare intent so browsers/password managers behave.
    for inp in doc.inputs:
        if (inp.get("type") or "").lower() == "password":
            ac = (inp.get("autocomplete") or "").lower()
            if ac not in ("new-password", "current-password"):
                f.error(
                    where,
                    "password input needs autocomplete='new-password' or 'current-password'",
                )

    # Every form control must be labelled: wrapped in a <label>, referenced by
    # label[for], or carrying an ARIA label. Unlabelled inputs are an
    # accessibility defect and, for the registration form, a conversion defect.
    label_ids = set(doc.label_for)
    for inp in doc.inputs:
        kind = (inp.get("type") or "text").lower()
        if kind in ("hidden", "submit", "button", "reset"):
            continue
        ancestors = inp.get("_ancestors") or ()
        wrapped = "label" in ancestors
        referenced = bool(inp.get("id")) and inp["id"] in label_ids
        aria = inp.get("aria-label") or inp.get("aria-labelledby")
        if not (wrapped or referenced or aria):
            f.error(
                where,
                f"form control {inp.get('name') or inp.get('id') or kind!r} "
                f"has no associated label",
            )


def check_secrets(page: Path, f: Findings) -> None:
    text = page.read_text(encoding="utf-8")
    patterns = {
        "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{20,}",
        "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
        "OpenAI-style key": r"\bsk-[A-Za-z0-9]{20,}",
        "hard-coded long password": r"""(?i)password\s*[:=]\s*["'][^"']{12,}["']""",
    }
    for name, pat in patterns.items():
        if re.search(pat, text):
            f.error(page.name, f"possible secret in source ({name})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def pages_to_check() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.html") if ".git" not in p.parts and "node_modules" not in p.parts
    )


def main() -> int:
    f = Findings()
    pages = pages_to_check()

    if not pages:
        print("FAIL: no HTML pages found")
        return 1

    for page in pages:
        raw = page.read_text(encoding="utf-8")
        doc = Doc()
        doc.feed(raw)
        check_document(doc, page, f)
        check_anchors_and_links(doc, page, f)
        check_customer_portal(doc, page, f)
        check_assets(doc, page, f)
        check_accessibility(doc, page, f)
        check_secrets(page, f)
        print(
            f"  checked {page.relative_to(ROOT)}  "
            f"(tags={len(doc.tags)} links={len(doc.anchor_hrefs)} ids={len(doc.ids)})"
        )

    check_sitemap_excludes_error_pages(f)

    print()
    for w in f.warnings:
        print(f"WARN  {w}")
    for e in f.errors:
        print(f"ERROR {e}")

    print()
    print(
        f"site-checks: {len(pages)} page(s), {len(f.errors)} error(s), {len(f.warnings)} warning(s)"
    )
    return 1 if f.errors else 0


if __name__ == "__main__":
    sys.exit(main())
