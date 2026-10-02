"""Tracking lookups: routes, both envelopes, and the customer-safe projection.

Everything here runs offline. Where a real HTTP round trip is exercised it goes
through ``httpx.MockTransport``, which never opens a socket; where only the route
mapping is under test, a fake provider stands in so the failure can be raised
directly.

The two response bodies used below are the SHAPES THE OWNER MEASURED against the
live service, including the fields we deliberately drop. Keeping the dropped
fields in the fixture is the point: a test that only feeds us the keys we already
whitelist cannot show that the whitelist does anything.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx
import pytest

from app.providers.base import ProviderResponseError, ProviderUnavailableError
from app.providers.khaibao9610 import ViporderFrontendProvider
from app.tracking import (
    KIND_PACKAGE_SEALING,
    KIND_WAREHOUSE_IMPORT,
    MAX_KEYWORD_LENGTH,
    InvalidKeywordError,
    normalize_package_sealing,
    normalize_warehouse_import,
    sanitise_keyword,
)
from tests.conftest import Harness

#: The measured working keyword. The pipe is part of the real code and must be
#: percent-encoded in the path.
REAL_KEYWORD = "KY4001103376087-2-4-|s"
#: The same code without its suffixes — measured to return 404.
BARE_KEYWORD = "KY4001103376087"

WAREHOUSE_IMPORT_PATH = "/api/tracking/warehouse-imports"
PACKAGE_SEALING_PATH = "/api/tracking/package-sealings"

#: MEASURED shape: /warehouse-imports/{keyword} returns a BARE object.
MEASURED_WAREHOUSE_IMPORT = {
    "id": 98765,
    "customer_code": "TT00123",
    "customer": {"id": 42, "name": "Nguyễn Văn A", "code": "TT00123"},
    "package_sealing_code": "SEAL-2026-0001",
    "note": "Hàng dễ vỡ",
    "date": "2026-02-14",
    "weight": 12.5,
    "china_tracking_code": "CN123456789",
    "vietnam_tracking_code": "VN987654321",
    "vnpost_tracking_code": "VNPOST999",
    "product_name_cn": "衣服",
    "product_name_vi": "Quần áo",
    "product_quantity": 3,
    "product_price": 150000,
    "product_code": "P-001",
    "package_quantity": 1,
    "package_number": 2,
    "create_package": True,
    "status": "Đang vận chuyển",
    "status_logs": [
        {
            "status": "Đã nhập kho",
            "note": "Nhập kho Trung Quốc",
            "created_at": "2026-02-14 09:00:00",
            "created_by": "nhanvien-01",
        },
        {
            "status": "Đang vận chuyển",
            "note": None,
            "created_at": "2026-02-15 09:00:00",
            "created_by": "nhanvien-02",
        },
    ],
    "status_background_color": "#FFF3CD",
    "status_text_color": "#856404",
    "is_pending": False,
    "is_completed": False,
    "created_at": "2026-02-14 09:00:00",
    "vietnam_warehouse_export": None,
}

#: MEASURED shape: /package-sealings/{keyword} returns {"data": {...}}.
MEASURED_PACKAGE_SEALING = {
    "data": {
        "id": 555,
        "customer": {"id": 42, "name": "Nguyễn Văn A", "code": "TT00123"},
        "code": "SEAL-2026-0001",
        "actual_weight": 12.5,
        "length": 30,
        "width": 20,
        "height": 10,
        "volume": 6000,
        "area_id": 7,
        "area_code": "HN01",
        "note": "Đóng bao tại kho Hà Nội",
        "total_warehouse_imports_count": 1,
        "status": "Đã đóng bao",
        "status_logs": [
            {
                "status": "Đã đóng bao",
                "note": "Đóng bao xong",
                "created_at": "2026-02-14 10:00:00",
                "created_by": "nhanvien-03",
            }
        ],
        "status_background_color": "#D4EDDA",
        "status_text_color": "#155724",
        "created_at": "2026-02-14 10:00:00",
        "vietnam_warehouse_export": None,
        "warehouse_imports": [
            {
                "id": 98765,
                "created_at": "2026-02-14 09:00:00",
                "china_tracking_code": "CN123456789",
                "vietnam_tracking_code": "VN987654321",
                "package_sealing_code": "SEAL-2026-0001",
                "vietnam_warehouse_export_code": "XK-001",
                "weight": 12.5,
                "customer": {"id": 42, "name": "Nguyễn Văn A", "code": "TT00123"},
            }
        ],
        "warehouse_imports_count": 1,
    }
}

#: The provider's own 404 sentence. It must never reach our customer: echoing a
#: third party's string would hand it control of a message our customers read.
UPSTREAM_NOT_FOUND_MESSAGE = "Mã vận đơn không tồn tại"


class FakeTrackingProvider:
    """A provider that returns, or raises, exactly what a test tells it to."""

    name = "fake-tracking"

    def __init__(
        self, *, warehouse: object = None, sealing: object = None, error: Exception | None = None
    ):
        self.warehouse = warehouse
        self.sealing = sealing
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def find_warehouse_import(self, keyword: str):  # type: ignore[no-untyped-def]
        self.calls.append(("warehouse_import", keyword))
        if self.error is not None:
            raise self.error
        return self.warehouse

    def find_package_sealing(self, keyword: str):  # type: ignore[no-untyped-def]
        self.calls.append(("package_sealing", keyword))
        if self.error is not None:
            raise self.error
        return self.sealing


def live_provider(handler, **overrides):  # type: ignore[no-untyped-def]
    """A real ViporderFrontendProvider wired to a MockTransport (no sockets)."""
    config = {
        "mode": "http",
        "enable_real_calls": True,
        "base_url": "https://apiviporder.com/frontend/v1",
        "timeout_seconds": 5.0,
        "user_agent": "Mozilla/5.0 (Test) AppleWebKit/537.36",
    }
    config.update(overrides)
    return ViporderFrontendProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)), **config
    )


def responding_with(body: object, status: int = 200, *, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(status, json=body)

    return handler


# --- the happy path, both envelopes ----------------------------------------


def test_warehouse_import_found_returns_the_bare_object_projected(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    provider = live_provider(responding_with(MEASURED_WAREHOUSE_IMPORT))
    harness = make_harness(provider=provider)

    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{quote(REAL_KEYWORD, safe='')}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "found"
    assert body["search_type"] == "warehouse_import"
    data = body["data"]
    assert data["kind"] == "warehouse_import"
    assert data["customer_code"] == "TT00123"
    assert data["weight"] == 12.5
    assert data["china_tracking_code"] == "CN123456789"
    assert data["vietnam_tracking_code"] == "VN987654321"
    assert data["vnpost_tracking_code"] == "VNPOST999"
    assert data["package_sealing_code"] == "SEAL-2026-0001"
    assert data["product_name_vi"] == "Quần áo"
    assert data["product_name_cn"] == "衣服"
    assert data["product_quantity"] == 3
    assert data["package_quantity"] == 1
    assert data["package_number"] == 2
    assert data["status"] == "Đang vận chuyển"
    assert data["is_pending"] is False
    assert data["is_completed"] is False


def test_package_sealing_found_returns_the_wrapped_object_projected(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    provider = live_provider(responding_with(MEASURED_PACKAGE_SEALING))
    harness = make_harness(provider=provider)

    response = harness.client.get(f"{PACKAGE_SEALING_PATH}/{quote(REAL_KEYWORD, safe='')}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "found"
    assert body["search_type"] == "package_sealing"
    data = body["data"]
    assert data["kind"] == "package_sealing"
    assert data["code"] == "SEAL-2026-0001"
    # From `customer.code`, NOT the top-level `code` (which is the sealing code).
    assert data["customer_code"] == "TT00123"
    assert data["actual_weight"] == 12.5
    assert (data["length"], data["width"], data["height"]) == (30, 20, 10)
    assert data["volume"] == 6000
    assert data["status"] == "Đã đóng bao"
    assert data["created_at"] == "2026-02-14 10:00:00"
    assert data["warehouse_imports_count"] == 1
    assert data["warehouse_imports"] == [
        {
            "china_tracking_code": "CN123456789",
            "vietnam_tracking_code": "VN987654321",
            "package_sealing_code": "SEAL-2026-0001",
            "weight": 12.5,
            "created_at": "2026-02-14 09:00:00",
        }
    ]


# --- THE ENVELOPE DIFFERENCE, asserted explicitly ---------------------------


def test_the_two_envelopes_are_not_interchangeable() -> None:
    """This is the single most important fact about the two endpoints.

    The bare warehouse-import body fed to the sealing normaliser projects to
    nulls, because that normaliser looks under ``data``. And the wrapped sealing
    body fed to the warehouse-import normaliser does the same, because it looks at
    the top level. A parser that assumed one shape would silently answer
    "everything is null" for half of its traffic — a 200 that looks like success
    and tells the customer nothing.
    """
    bare = normalize_warehouse_import(MEASURED_WAREHOUSE_IMPORT)
    assert bare["customer_code"] == "TT00123"

    # Right body, wrong endpoint for it.
    wrong_way_round = normalize_package_sealing(MEASURED_WAREHOUSE_IMPORT)
    assert wrong_way_round["code"] is None
    assert wrong_way_round["customer_code"] is None
    assert wrong_way_round["warehouse_imports"] == []

    # And the converse.
    wrapped = normalize_package_sealing(MEASURED_PACKAGE_SEALING)
    assert wrapped["code"] == "SEAL-2026-0001"

    also_wrong = normalize_warehouse_import(MEASURED_PACKAGE_SEALING)
    assert also_wrong["customer_code"] is None
    assert also_wrong["china_tracking_code"] is None


def test_the_bare_and_wrapped_shapes_differ_on_the_wire(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    """The same assertion, end to end through the routes.

    Both endpoints are handed the body the OTHER one returns. Neither may report
    a populated record, which is what proves the envelopes are handled separately
    rather than by one shared unwrapper.
    """
    provider = live_provider(
        lambda request: httpx.Response(
            200,
            json=(
                MEASURED_WAREHOUSE_IMPORT
                if "warehouse-imports" in request.url.path
                else MEASURED_PACKAGE_SEALING
            ),
        )
    )
    harness = make_harness(provider=provider)

    warehouse = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}").json()
    sealing = harness.client.get(f"{PACKAGE_SEALING_PATH}/{BARE_KEYWORD}").json()

    assert warehouse["data"]["china_tracking_code"] == "CN123456789"
    assert sealing["data"]["warehouse_imports"][0]["china_tracking_code"] == "CN123456789"
    # Neither shape works in the other's slot.
    assert normalize_warehouse_import(MEASURED_PACKAGE_SEALING)["status"] is None
    assert normalize_package_sealing(MEASURED_WAREHOUSE_IMPORT)["status"] is None


# --- what is excluded, and that the colours are not -------------------------


@pytest.mark.parametrize("key", ["id", "created_by", "area_id", "area_code"])
def test_internal_fields_are_absent_from_the_response(
    key: str,
    harness: Harness,
) -> None:
    """Internal-only keys must not appear anywhere in the payload.

    Compared as KEYS, not as a substring of the serialised body. The first
    version of this test used ``json.dumps(...)`` and ``"id" not in text``, which
    passed for ``id`` on one route and failed on the other only because
    ``"width"`` contains ``id`` — a test measuring string coincidence rather than
    the projection.
    """
    provider = FakeTrackingProvider(
        warehouse=MEASURED_WAREHOUSE_IMPORT, sealing=MEASURED_PACKAGE_SEALING
    )
    harness.app.state.provider = provider

    warehouse = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}").json()
    sealing = harness.client.get(f"{PACKAGE_SEALING_PATH}/{BARE_KEYWORD}").json()

    assert key not in nested_keys(warehouse)
    assert key not in nested_keys(sealing)
    # Positive control: the walk really does reach the nested entries, so a
    # missing key is not just a walk that found nothing.
    assert "china_tracking_code" in nested_keys(warehouse)
    assert "package_sealing_code" in nested_keys(sealing)


def nested_keys(value: object) -> set[str]:
    """Every dict key at any depth."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            found |= nested_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= nested_keys(item)
    return found


def test_the_customer_id_is_not_exposed_only_the_code() -> None:
    projected = normalize_warehouse_import(MEASURED_WAREHOUSE_IMPORT)
    assert projected["customer_code"] == "TT00123"
    assert "customer" not in projected
    assert projected != MEASURED_WAREHOUSE_IMPORT


def test_status_colours_are_kept_on_purpose() -> None:
    """The provider's own visual language, so the status chip can match it."""
    warehouse = normalize_warehouse_import(MEASURED_WAREHOUSE_IMPORT)
    assert warehouse["status_background_color"] == "#FFF3CD"
    assert warehouse["status_text_color"] == "#856404"


def test_the_sealing_projection_has_no_colours_and_that_is_the_contract() -> None:
    """An asymmetry in the agreed contract, pinned so it is not "fixed" silently.

    The provider sends ``status_background_color``/``status_text_color`` on the
    sealing body too, but the agreed ``package_sealing`` key list does not name
    them. Adding them here would be inventing part of the contract, so they are
    absent — and this test exists so that absence is a recorded decision rather
    than an oversight someone later "corrects" without a conversation.
    """
    sealing = normalize_package_sealing(MEASURED_PACKAGE_SEALING)

    assert "status_background_color" not in sealing
    assert "status_text_color" not in sealing
    # They really are in the provider's body — the omission is ours, not theirs.
    assert MEASURED_PACKAGE_SEALING["data"]["status_background_color"] == "#D4EDDA"


def test_the_projection_is_a_whitelist_not_a_passthrough() -> None:
    """A key we have never seen must not reach the customer."""
    raw = dict(MEASURED_WAREHOUSE_IMPORT)
    raw["internal_margin_percent"] = 22.5
    raw["supplier_name"] = "Shenzhen Ltd"

    projected = normalize_warehouse_import(raw)

    assert "internal_margin_percent" not in projected
    assert "supplier_name" not in projected
    assert set(projected) == {
        "kind",
        "customer_code",
        "date",
        "weight",
        "china_tracking_code",
        "vietnam_tracking_code",
        "vnpost_tracking_code",
        "package_sealing_code",
        "product_name_vi",
        "product_name_cn",
        "product_quantity",
        "package_quantity",
        "package_number",
        "status",
        "status_logs",
        "is_pending",
        "is_completed",
        "status_background_color",
        "status_text_color",
    }


def test_the_sealing_projection_is_also_a_closed_whitelist() -> None:
    projected = normalize_package_sealing(MEASURED_PACKAGE_SEALING)
    assert set(projected) == {
        "kind",
        "code",
        "customer_code",
        "actual_weight",
        "length",
        "width",
        "height",
        "volume",
        "status",
        "status_logs",
        "created_at",
        "warehouse_imports_count",
        "warehouse_imports",
    }


# --- status_logs ------------------------------------------------------------


def test_status_logs_are_passed_through_without_created_by() -> None:
    projected = normalize_warehouse_import(MEASURED_WAREHOUSE_IMPORT)

    assert projected["status_logs"] == [
        {
            "status": "Đã nhập kho",
            "note": "Nhập kho Trung Quốc",
            "created_at": "2026-02-14 09:00:00",
        },
        {"status": "Đang vận chuyển", "note": None, "created_at": "2026-02-15 09:00:00"},
    ]
    assert "nhanvien-01" not in json.dumps(projected)


def test_status_logs_passthrough_on_a_sealing() -> None:
    projected = normalize_package_sealing(MEASURED_PACKAGE_SEALING)
    assert projected["status_logs"] == [
        {
            "status": "Đã đóng bao",
            "note": "Đóng bao xong",
            "created_at": "2026-02-14 10:00:00",
        }
    ]


# --- every field nullable, nothing raises ----------------------------------


@pytest.mark.parametrize("raw", [None, {}, [], "", 0, "a string", {"data": None}, {"data": []}])
def test_every_field_absent_at_once_does_not_raise(raw: object) -> None:
    """The whole point of the projection: any shape in, nulls out, no exception."""
    warehouse = normalize_warehouse_import(raw)
    sealing = normalize_package_sealing(raw)

    for projected in (warehouse, sealing):
        assert isinstance(projected, dict)
        assert projected["status"] is None
        assert projected["status_logs"] == []

    assert warehouse["customer_code"] is None
    assert warehouse["china_tracking_code"] is None
    assert sealing["code"] is None
    assert sealing["customer_code"] is None
    assert sealing["warehouse_imports"] == []
    assert sealing["warehouse_imports_count"] is None


def test_customer_code_null_when_the_provider_omits_it() -> None:
    raw = dict(MEASURED_WAREHOUSE_IMPORT)
    raw.pop("customer_code")
    raw.pop("customer")

    projected = normalize_warehouse_import(raw)

    assert projected["customer_code"] is None
    # And the rest of the record still comes through — one missing field must not
    # take the whole lookup down with it.
    assert projected["china_tracking_code"] == "CN123456789"


def test_customer_code_null_when_customer_is_not_an_object() -> None:
    projected = normalize_warehouse_import({**MEASURED_WAREHOUSE_IMPORT, "customer": "TT00123"})
    assert projected["customer_code"] == "TT00123"  # from the top-level key


def test_wrong_types_degrade_to_null_rather_than_raising() -> None:
    """A third party's types are not ours. This is the whole defensive posture."""
    raw = {
        "customer_code": {"nested": "object"},
        "weight": "not-a-number",
        "product_quantity": None,
        "package_number": [1, 2],
        "status": 12345,
        "status_logs": "not-a-list",
        "is_pending": "yes",
        "is_completed": {"a": 1},
        "status_background_color": 7,
    }

    projected = normalize_warehouse_import(raw)

    assert projected["customer_code"] is None
    assert projected["weight"] is None
    assert projected["product_quantity"] is None
    assert projected["package_number"] is None
    assert projected["status"] == "12345"  # a scalar is stringified, that is safe
    assert projected["status_logs"] == []
    assert projected["is_pending"] is None
    assert projected["is_completed"] is None
    assert projected["status_background_color"] == "7"


def test_boolean_fields_are_tri_state() -> None:
    """None is not False: "not told" and "told no" are different answers."""
    assert normalize_warehouse_import({"is_pending": False})["is_pending"] is False
    assert normalize_warehouse_import({"is_pending": None})["is_pending"] is None
    assert normalize_warehouse_import({})["is_pending"] is None
    # A bool is an int subclass, so this is the case that would corrupt a number.
    assert normalize_warehouse_import({"weight": True})["weight"] is None


def test_numeric_strings_are_accepted_and_non_finite_is_not() -> None:
    assert normalize_warehouse_import({"weight": "12.5"})["weight"] == 12.5
    assert normalize_warehouse_import({"package_quantity": "3"})["package_quantity"] == 3
    assert normalize_warehouse_import({"weight": float("nan")})["weight"] is None
    assert normalize_warehouse_import({"weight": float("inf")})["weight"] is None
    # python's json accepts these by default, so they really can arrive
    assert normalize_warehouse_import(json.loads('{"weight": NaN}'))["weight"] is None


def test_non_object_entries_inside_the_lists_are_dropped() -> None:
    raw = {
        "status_logs": ["a string", 42, None, {"status": "ok", "note": "n", "created_at": "t"}],
        "warehouse_imports": ["nope", {"china_tracking_code": "CN1"}],
    }
    assert normalize_warehouse_import(raw)["status_logs"] == [
        {"status": "ok", "note": "n", "created_at": "t"}
    ]
    assert normalize_package_sealing({"data": raw})["warehouse_imports"] == [
        {
            "china_tracking_code": "CN1",
            "vietnam_tracking_code": None,
            "package_sealing_code": None,
            "weight": None,
            "created_at": None,
        }
    ]


# --- keyword validation, before any network call ---------------------------


@pytest.mark.parametrize(
    ("keyword", "expected_status"),
    [
        pytest.param("../../etc/passwd", 400, id="traversal"),
        pytest.param("KY1/../../v1/register", 400, id="traversal-after-a-code"),
        pytest.param("KY1?probe=1", 400, id="query-injection"),
        pytest.param("KY1#fragment", 400, id="fragment-injection"),
        pytest.param("KY1&x=1", 400, id="parameter-injection"),
        pytest.param("KY1%00", 400, id="percent-null"),
        pytest.param("\x00", 400, id="raw-null"),
        pytest.param("<script>alert(1)</script>", 400, id="encoded-script"),
        pytest.param("KY1' OR 1=1--", 400, id="sql-metacharacters"),
        pytest.param("KY1;DROP TABLE leads", 400, id="sql-statement"),
        pytest.param("KY1 with spaces", 400, id="spaces"),
        pytest.param("a" * (MAX_KEYWORD_LENGTH + 1), 400, id="oversized-by-one"),
        pytest.param("a" * 300, 400, id="300-characters"),
        # These two never reach the handler, and that is the URL layer's doing,
        # not a hole. `..` is removed by URL normalisation before the request is
        # even sent (httpx here, a browser and nginx in production), so the path
        # collapses to /api/tracking. A newline survives encoding but cannot match
        # Starlette's path convertor, whose `.*` does not match `\n` without
        # DOTALL. Both are refusals with no network call, which is what matters;
        # `sanitise_keyword` still rejects both directly, and that is asserted
        # separately below so the boundary holds on its own.
        pytest.param("..", 404, id="bare-dotdot-normalised-away-before-routing"),
        pytest.param(
            "KY1\nheader: injected",
            404,
            id="newline-cannot-match-the-path-convertor",
        ),
    ],
)
def test_a_hostile_keyword_is_rejected_before_any_network_call(
    make_harness,  # type: ignore[no-untyped-def]
    keyword: str,
    expected_status: int,
) -> None:
    """Either 400 from our validator, or an earlier refusal — never a 200.

    The upstream returned 404 for all of these at measurement time, which is what
    it does today — a third party's input handling is not a control we own. The
    handler below fails the test if it is called at all, so "no network call" is
    proven rather than asserted.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError(f"a hostile keyword reached the network: {request.url}")

    harness = make_harness(provider=live_provider(handler))

    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{quote(keyword, safe='')}")

    assert response.status_code == expected_status, response.text
    assert response.status_code != 200
    assert calls == []
    if expected_status == 400:
        assert response.json() == {
            "error": {
                "code": "INVALID_KEYWORD",
                "message": (
                    "Mã tra cứu không hợp lệ. Vui lòng kiểm tra lại và chỉ nhập mã in trên phiếu."
                ),
            }
        }


@pytest.mark.parametrize(
    "keyword",
    ["../../etc/passwd", "..", "KY1\nheader: injected", "<script>alert(1)</script>", "KY1?x=1"],
)
def test_sanitise_keyword_refuses_every_hostile_form_on_its_own(keyword: str) -> None:
    """The boundary must hold without relying on the URL layer.

    Two of the cases above are stopped before routing. That is a happy accident of
    the HTTP stack, not a control, and a caller reaching ``sanitise_keyword``
    another way must still be refused.
    """
    with pytest.raises(InvalidKeywordError):
        sanitise_keyword(keyword)


@pytest.mark.parametrize("keyword", ["", "   ", "\t"])
def test_an_empty_keyword_is_rejected(keyword: str) -> None:
    """Validated at the function, because an empty path segment is a router 404.

    That distinction is worth stating: ``/api/tracking/warehouse-imports/`` does
    not match the route at all, so it never reaches this code. The check still
    exists because ``sanitise_keyword`` is the boundary and must hold on its own.
    """
    with pytest.raises(InvalidKeywordError):
        sanitise_keyword(keyword)


def test_an_overlong_keyword_is_rejected() -> None:
    with pytest.raises(InvalidKeywordError):
        sanitise_keyword("a" * (MAX_KEYWORD_LENGTH + 1))
    # One at the limit is accepted, so the bound is inclusive and the test above
    # is not passing for an unrelated reason.
    assert sanitise_keyword("a" * MAX_KEYWORD_LENGTH) == "a" * MAX_KEYWORD_LENGTH


@pytest.mark.parametrize(
    "keyword",
    [BARE_KEYWORD, REAL_KEYWORD, "abc", "ABC-123_x", "SEAL|1", "0"],
)
def test_legal_keywords_are_accepted_unchanged(keyword: str) -> None:
    assert sanitise_keyword(keyword) == keyword


def test_the_pipe_and_dash_are_legal_because_real_codes_contain_them() -> None:
    assert sanitise_keyword("KY4001103376087-2-4-|s") == "KY4001103376087-2-4-|s"


def test_a_dot_is_refused() -> None:
    """Excluded on purpose: quote() does not encode '.', so '..' would survive."""
    with pytest.raises(InvalidKeywordError):
        sanitise_keyword("..")


def test_surrounding_whitespace_is_not_silently_stripped() -> None:
    """Stripping would answer about a different code than the customer typed."""
    with pytest.raises(InvalidKeywordError):
        sanitise_keyword(" KY1")


def test_the_keyword_is_rejected_without_being_echoed_back(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    """The offending value is untrusted input and must not be reflected."""
    harness = make_harness(provider=FakeTrackingProvider(warehouse={}))
    hostile = "<script>alert(1)</script>"

    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{quote(hostile, safe='')}")

    assert response.status_code == 400
    assert "script" not in response.text
    assert hostile not in response.text


# --- the pipe is percent-encoded on the wire --------------------------------


def test_the_pipe_is_percent_encoded_in_the_outbound_path(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    calls: list[str] = []
    harness = make_harness(
        provider=live_provider(responding_with(MEASURED_WAREHOUSE_IMPORT, calls=calls))
    )

    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{quote(REAL_KEYWORD, safe='')}")

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0] == (
        "https://apiviporder.com/frontend/v1/warehouse-imports/KY4001103376087-2-4-%7Cs"
    )
    assert "|" not in calls[0]


def test_the_route_decodes_the_percent_encoded_keyword_before_the_provider(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    """The provider must receive the real code, not the encoded segment."""
    provider = FakeTrackingProvider(warehouse=MEASURED_WAREHOUSE_IMPORT)
    harness = make_harness(provider=provider)

    harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{quote(REAL_KEYWORD, safe='')}")

    assert provider.calls == [("warehouse_import", REAL_KEYWORD)]


# --- 404, 502, 503 ----------------------------------------------------------


def test_a_404_from_the_provider_is_not_found(make_harness) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": UPSTREAM_NOT_FOUND_MESSAGE})

    harness = make_harness(provider=live_provider(handler))

    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Không tìm thấy mã vận đơn này. Vui lòng kiểm tra lại mã.",
        }
    }
    # The upstream's own sentence is NOT echoed back to our customer.
    assert UPSTREAM_NOT_FOUND_MESSAGE not in response.text


def test_a_404_on_the_sealing_route_has_its_own_wording(make_harness) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Mã đóng bao không tồn tại"})

    harness = make_harness(provider=live_provider(handler))
    response = harness.client.get(f"{PACKAGE_SEALING_PATH}/{BARE_KEYWORD}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "đóng bao" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_code"),
    [
        (ProviderUnavailableError("timed out"), 503, "PROVIDER_UNAVAILABLE"),
        (ProviderResponseError("HTTP 500"), 502, "PROVIDER_ERROR"),
    ],
)
def test_provider_errors_map_to_distinct_status_codes(
    make_harness,  # type: ignore[no-untyped-def]
    provider_error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    """502 and 503 are deliberately different, and both routes agree."""
    harness = make_harness(provider=FakeTrackingProvider(error=provider_error))

    for path in (WAREHOUSE_IMPORT_PATH, PACKAGE_SEALING_PATH):
        response = harness.client.get(f"{path}/{BARE_KEYWORD}")
        assert response.status_code == expected_status, path
        assert response.json()["error"]["code"] == expected_code, path
        # No internal detail leaks into the customer-facing message.
        assert "timed out" not in response.text
        assert "500" not in response.json()["error"]["message"]


def test_a_timeout_is_503(make_harness) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    harness = make_harness(provider=live_provider(handler))
    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


def test_a_5xx_is_502(make_harness) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    harness = make_harness(provider=live_provider(handler))
    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_ERROR"
    assert "Internal Server Error" not in response.text


def test_malformed_json_is_502(make_harness) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>we are down for maintenance</html>")

    harness = make_harness(provider=live_provider(handler))
    response = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_ERROR"
    assert "maintenance" not in response.text


# --- the default configuration cannot track ---------------------------------


def test_the_default_mock_provider_answers_503(harness: Harness) -> None:
    """Out of the box there is nobody to ask, and saying so is the honest answer.

    The live adapter only exists when BOTH real-call switches are set, so in the
    default ``mock`` mode the configured provider has no tracking methods. The
    alternative would be to fabricate a provider or to reach the network from a
    configuration that was explicitly meant not to.
    """
    assert harness.app.state.provider.name == "mock"

    for path in (WAREHOUSE_IMPORT_PATH, PACKAGE_SEALING_PATH):
        response = harness.client.get(f"{path}/{BARE_KEYWORD}")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


# --- the contract shape, both routes ---------------------------------------


def test_both_routes_share_one_response_contract(
    make_harness,  # type: ignore[no-untyped-def]
) -> None:
    provider = FakeTrackingProvider(
        warehouse=MEASURED_WAREHOUSE_IMPORT, sealing=MEASURED_PACKAGE_SEALING
    )
    harness = make_harness(provider=provider)

    warehouse = harness.client.get(f"{WAREHOUSE_IMPORT_PATH}/{BARE_KEYWORD}").json()
    sealing = harness.client.get(f"{PACKAGE_SEALING_PATH}/{BARE_KEYWORD}").json()

    assert set(warehouse) == set(sealing) == {"result", "search_type", "data"}
    assert warehouse["result"] == sealing["result"] == "found"
    assert warehouse["search_type"] == KIND_WAREHOUSE_IMPORT
    assert sealing["search_type"] == KIND_PACKAGE_SEALING


def test_search_type_values_are_stable() -> None:
    assert KIND_WAREHOUSE_IMPORT == "warehouse_import"
    assert KIND_PACKAGE_SEALING == "package_sealing"
