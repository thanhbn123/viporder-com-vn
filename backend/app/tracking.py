"""Tracking lookups: keyword validation and the customer-safe projection.

WHAT WAS MEASURED, AND WHEN
---------------------------
The endpoints below were measured against the live service
(``https://apiviporder.com/frontend/v1``) by the owner. The findings that shape
this module:

1. **The two envelopes differ.** ``GET /warehouse-imports/{keyword}`` answers with
   a BARE object. ``GET /package-sealings/{keyword}`` answers with
   ``{"data": {...}}``. A parser that assumes one shape fails on the other, so
   unwrapping happens in exactly one place — here — rather than in the provider,
   the route, and the tests, where the two would eventually drift.

2. **The working keyword is the full tracking code.** ``KY4001103376087`` returns
   404; ``KY4001103376087-2-4-|s`` returns 200. The pipe is part of a real code,
   so ``|`` and ``-`` are inside the allowed set, and the pipe must be
   percent-encoded in the path.

3. **Auth was not needed at measurement time.** ``GET /warehouse-imports`` (the
   bare list) returned ``401 {"error":"Unauthenticated."}`` while the keyword form
   returned 200 with no credentials. That is OBSERVED AT MEASUREMENT TIME, not a
   guarantee: upstream authentication can change without notice, so a 401 from
   the keyword endpoint is treated as a contract change (502), never as "the code
   does not exist".

4. **Hostile keywords all returned 404** without reaching anything — traversal,
   NUL, encoded script, SQL metacharacters, 300 characters. That is what the
   upstream does *today*. We still validate on our side: a third party's input
   handling is not a control we own, and the request is on our behalf.

WHAT IS EXPOSED, AND WHAT IS NOT
--------------------------------
Every projection below is a **whitelist**. The provider's object is never passed
through, and no key reaches a customer because someone upstream decided to add
one — a new column in the provider's response stays invisible until someone
edits this file on purpose.

Excluded, with the reason each is excluded:

* ``id`` — the provider's internal row identifier. It is of no use to a customer,
  and handing out a sequential internal id invites probing that has nothing to do
  with the tracking code the customer holds.
* ``customer.id`` — same reasoning; the customer-facing handle is
  ``customer.code``.
* ``created_by`` (on ``status_logs``) — identifies a member of staff. Who at the
  company touched a parcel is internal operational data.
* ``area_id`` / ``area_code`` — internal warehouse/area identifiers. They describe
  how the company is organised, not where the customer's parcel is.
* ``vietnam_warehouse_export.id`` and ``vietnam_warehouse_export.customer.id`` —
  internal row ids, same reasoning as ``id``. Only the delivery CODE and the
  delivery customer's code and name are kept (owner, 2026-10-10: the old
  tracking page shows "Mã giao hàng" and "Khách giao hàng").

**Kept on purpose:** ``status_background_color`` and ``status_text_color``. These
look like presentation concerns and are usually the first thing to strip, but
they are the provider's own visual language for a status. Keeping them means the
customer-facing status chip can match the colours the customer already sees in
the provider's own system, instead of this service inventing a second colour
scheme that drifts from it.

**An asymmetry worth knowing about:** the agreed contract lists the two colour
keys for ``warehouse_import`` but NOT for ``package_sealing``, even though the
provider sends them on both. That is reproduced faithfully here rather than
"tidied up", because adding a key the contract does not name is inventing one.
The consequence for the front end is real and is called out so it is a decision
rather than a surprise: a parcel's status chip can use the provider's colours, a
sealing's cannot.

EVERY FIELD IS NULLABLE
-----------------------
A provider is a third party and its types are not ours. A field that arrives with
the wrong type degrades to ``null``; a field that is absent stays ``null``. No
shape of input raises: the projection's job is to be safe to serialise, and a
route that 500s because a third party sent an integer where the documentation
said string is a worse outcome than a null the UI can render.
"""

from __future__ import annotations

import math
import re

#: The longest keyword we accept. Real codes measured are ~20 characters. 64
#: leaves generous room (a code plus its ``-2-4-|s`` suffixes) while keeping the
#: value far away from anything a path or a log line cares about.
MAX_KEYWORD_LENGTH = 64

#: The characters a tracking code may contain.
#:
#: ``|`` and ``-`` are here because they are in a REAL code
#: (``KY4001103376087-2-4-|s``). Letters and digits are the body of every code
#: seen. ``_`` is allowed as the harmless separator it would be.
#:
#: ``.`` IS DELIBERATELY ABSENT, and that is the one non-obvious choice.
#: ``urllib.parse.quote`` does not encode ``.`` — it is an unreserved URL
#: character — so a keyword of ``..`` would survive encoding as a literal ``..``
#: path segment and let the keyword walk up the URL path. Excluding the dot
#: removes that class of input entirely, at the cost of rejecting a code
#: containing a dot, which no measured code does.
ALLOWED_KEYWORD_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-|_"
)

_ALLOWED_KEYWORD_RE = re.compile(rf"^[{re.escape(''.join(sorted(ALLOWED_KEYWORD_CHARACTERS)))}]+$")

KIND_WAREHOUSE_IMPORT = "warehouse_import"
KIND_PACKAGE_SEALING = "package_sealing"


class InvalidKeywordError(ValueError):
    """The keyword is not something we are willing to put in a URL.

    Raised before any network call, so a hostile keyword costs one regex and
    never becomes an outbound request made on our behalf.
    """


def sanitise_keyword(raw: str | None) -> str:
    """Validate a tracking keyword, or raise :class:`InvalidKeywordError`.

    Rejects, in order: a missing value, an empty or whitespace-only value,
    anything longer than :data:`MAX_KEYWORD_LENGTH`, and anything containing a
    character outside :data:`ALLOWED_KEYWORD_CHARACTERS`.

    The value is returned **unchanged**. It is not stripped and not normalised:
    stripping would quietly turn a rejected ``"  KY1"`` into an accepted
    ``"KY1"``, and a lookup that silently answers about a different code than the
    one the customer typed is worse than a 400.
    """
    if raw is None:
        raise InvalidKeywordError("empty keyword")
    if not raw or not raw.strip():
        raise InvalidKeywordError("empty keyword")
    if len(raw) > MAX_KEYWORD_LENGTH:
        raise InvalidKeywordError(f"keyword longer than {MAX_KEYWORD_LENGTH} characters")
    if not _ALLOWED_KEYWORD_RE.match(raw):
        raise InvalidKeywordError("keyword contains characters outside the safe set")
    return raw


# --- value coercion ---------------------------------------------------------
#
# Every helper below returns None (or []) rather than raising. A bool is checked
# before int everywhere because ``isinstance(True, int)`` is True in Python, and a
# boolean leaking into a weight column as 1 would be a silent corruption.


def _text(value: object) -> str | None:
    """A trimmed string, or None. Numbers are stringified; containers are not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def _number(value: object) -> int | float | None:
    """An int or float, or None.

    Numeric strings are accepted because a Laravel-backed API may serialise a
    decimal as a string and that is not worth a null. ``NaN`` and ``inf`` are
    rejected: Python's ``json`` parses them by default, and a non-finite weight
    survives JSON encoding into something no client can render.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return int(candidate)
        except ValueError:
            pass
        try:
            parsed = float(candidate)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _flag(value: object) -> bool | None:
    """A tri-state boolean.

    Accepts real booleans, ``0``/``1``, and the strings ``"0"``/``"1"``/
    ``"true"``/``"false"``. Anything else — including a missing field — is None.
    None is *not* the same as False here: "the provider did not say" and "the
    provider said no" are different answers, and collapsing them is how a
    pending parcel starts rendering as a completed one.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true"):
            return True
        if lowered in ("0", "false"):
            return False
    return None


def _status_logs(value: object) -> list[dict]:
    """Project ``status_logs`` down to ``{status, note, created_at}``.

    Entries that are not objects are dropped rather than turned into null rows:
    there is no sensible projection of a string into a status entry, and an empty
    list is an honest answer. ``created_by`` — the one key deliberately removed
    from each entry — identifies a staff member; see the module docstring.
    """
    if not isinstance(value, list):
        return []
    logs: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        logs.append(
            {
                "status": _text(entry.get("status")),
                "note": _text(entry.get("note")),
                "created_at": _text(entry.get("created_at")),
            }
        )
    return logs


def _inner_object(raw: object) -> dict:
    """Coerce the raw provider body to a mapping, tolerating anything."""
    return raw if isinstance(raw, dict) else {}


def _delivery_customer(export: dict) -> str | None:
    """``"TT2840 - Thường xinh gái"``, as the old tracking page shows it.

    MEASURED 2026-10-10 on an exported parcel: ``vietnam_warehouse_export`` is
    ``{"id", "code", "customer": {"id", "code", "name"}}`` (null until the parcel
    leaves the Vietnam warehouse). Either half alone is shown on its own.
    """
    customer = _inner_object(export.get("customer"))
    parts = [p for p in (_text(customer.get("code")), _text(customer.get("name"))) if p]
    return " - ".join(parts) or None


# --- projections ------------------------------------------------------------


def normalize_warehouse_import(raw: object) -> dict:
    """Project a ``/warehouse-imports/{keyword}`` body for a customer.

    Input is the BARE object the endpoint returns. Passed anything else — None,
    a list, a string — every field comes back null instead of raising.
    """
    source = _inner_object(raw)
    customer = _inner_object(source.get("customer"))
    export = _inner_object(source.get("vietnam_warehouse_export"))
    return {
        "kind": KIND_WAREHOUSE_IMPORT,
        # Top-level on this envelope, NOT nested under `customer`.
        "customer_code": _text(source.get("customer_code")) or _text(customer.get("code")),
        "delivery_code": _text(export.get("code")),
        "delivery_customer": _delivery_customer(export),
        "date": _text(source.get("date")),
        "weight": _number(source.get("weight")),
        "china_tracking_code": _text(source.get("china_tracking_code")),
        "vietnam_tracking_code": _text(source.get("vietnam_tracking_code")),
        "vnpost_tracking_code": _text(source.get("vnpost_tracking_code")),
        "package_sealing_code": _text(source.get("package_sealing_code")),
        "product_name_vi": _text(source.get("product_name_vi")),
        "product_name_cn": _text(source.get("product_name_cn")),
        "product_quantity": _number(source.get("product_quantity")),
        "package_quantity": _number(source.get("package_quantity")),
        "package_number": _number(source.get("package_number")),
        "status": _text(source.get("status")),
        "status_logs": _status_logs(source.get("status_logs")),
        "is_pending": _flag(source.get("is_pending")),
        "is_completed": _flag(source.get("is_completed")),
        # Kept deliberately — see the module docstring.
        "status_background_color": _text(source.get("status_background_color")),
        "status_text_color": _text(source.get("status_text_color")),
    }


def normalize_package_sealing(raw: object) -> dict:
    """Project a ``/package-sealings/{keyword}`` body for a customer.

    Input is the FULL envelope (``{"data": {...}}``) as the provider returns it.
    A body with no usable ``data`` object projects to all-null rather than
    raising — the route has already decided the lookup succeeded by the time it
    gets here, and one unexpected shape must not become a 500.
    """
    envelope = _inner_object(raw)
    source = _inner_object(envelope.get("data"))
    customer = _inner_object(source.get("customer"))
    return {
        "kind": KIND_PACKAGE_SEALING,
        "code": _text(source.get("code")),
        # On this envelope the code lives at `customer.code`; the top-level key
        # is `code` and means the SEALING code, so it must not be reused here.
        "customer_code": _text(customer.get("code")),
        "actual_weight": _number(source.get("actual_weight")),
        "length": _number(source.get("length")),
        "width": _number(source.get("width")),
        "height": _number(source.get("height")),
        "volume": _number(source.get("volume")),
        "status": _text(source.get("status")),
        "status_logs": _status_logs(source.get("status_logs")),
        "created_at": _text(source.get("created_at")),
        "warehouse_imports_count": _number(source.get("warehouse_imports_count")),
        "warehouse_imports": _warehouse_imports(source.get("warehouse_imports")),
    }


def _warehouse_imports(value: object) -> list[dict]:
    """Project the nested parcel list on a sealing.

    Same rule as everywhere else: whitelist, and drop entries that are not
    objects. ``customer`` and ``id`` per parcel are dropped for the same reasons
    as at the top level.
    """
    if not isinstance(value, list):
        return []
    parcels: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        parcels.append(
            {
                "china_tracking_code": _text(entry.get("china_tracking_code")),
                "vietnam_tracking_code": _text(entry.get("vietnam_tracking_code")),
                "package_sealing_code": _text(entry.get("package_sealing_code")),
                # Flat on the nested parcels (measured 2026-10-02).
                "delivery_code": _text(entry.get("vietnam_warehouse_export_code")),
                "weight": _number(entry.get("weight")),
                "created_at": _text(entry.get("created_at")),
            }
        )
    return parcels
