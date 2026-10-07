"""G12 — the extractor must find a real customer code through ordinary envelopes.

WHY THIS EXISTS. `_extract` used to look at the top level plus exactly ONE layer of
`data`/`customer`/`result`. A response like

    {"data": {"customer": {"code": "TT00123"}}}

therefore returned `(None, None)`, and the registration was classified
`UNUSABLE_RESPONSE` — **a real customer code discarded and a success reported as a
contract problem**, which is the worst outcome this function can produce.

The wider search has to stay honest, so it descends only through named wrapper keys
and is depth- and node-capped. The tests below pin BOTH halves: the shapes that must
now be found, and the shapes that must NOT be guessed at.
"""

from __future__ import annotations

import pytest

from app.providers.khaibao9610 import ViporderFrontendProvider

extract = ViporderFrontendProvider._extract


# ---------------------------------------------------------------------------
# Must be FOUND — these are ordinary API envelopes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ({"data": {"customer": {"code": "TT1"}}}, "TT1"),
        ({"data": {"attributes": {"code": "TT2"}}}, "TT2"),
        ({"result": {"data": {"customer_code": "TT3"}}}, "TT3"),
        ({"customer": {"customer_code": "TT4"}}, "TT4"),
        ({"customer_info": {"code": "TT5"}}, "TT5"),
        ({"user": {"code": "TT6"}}, "TT6"),
        ({"account": {"ma_khach_hang": "TT7"}}, "TT7"),
        ({"record": {"code": "TT8"}}, "TT8"),
        ({"entry": {"code": "TT9"}}, "TT9"),
        ({"data": [{"customer_code": "TT10"}]}, "TT10"),
        ({"customer": [{"code": "TT11"}]}, "TT11"),
    ],
    ids=[
        "data.customer",
        "data.attributes",
        "result.data",
        "customer",
        "customer_info",
        "user",
        "account-vietnamese",
        "record",
        "entry",
        "data-is-a-list",
        "customer-is-a-list",
    ],
)
def test_a_code_inside_an_ordinary_envelope_is_found(body: object, expected_code: str) -> None:
    _identifier, code = extract(body)
    assert code == expected_code, f"{body!r} -> {code!r}"


def test_the_shallowest_code_wins() -> None:
    """A top-level code must not be displaced by a deeper one."""
    _identifier, code = extract({"customer_code": "TOP", "data": {"code": "DEEP"}})
    assert code == "TOP"


def test_a_pair_from_ONE_object_beats_a_lonely_code_higher_up() -> None:
    """A code and an id that arrived TOGETHER belong together.

    Top level has a code but no id; the wrapper has both. Taking the shallow code
    and a deeper id would pair two values that were never a pair.
    """
    identifier, code = extract({"customer_code": "SHALLOW", "data": {"code": "TT1", "id": "42"}})
    assert (identifier, code) == ("42", "TT1")


def test_the_shallowest_object_holding_BOTH_wins() -> None:
    """When the top level already has both, it is used and the walk stops."""
    identifier, code = extract(
        {"customer_code": "TOP", "id": "1", "data": {"code": "DEEP", "id": "2"}}
    )
    assert (identifier, code) == ("1", "TOP")


# ---------------------------------------------------------------------------
# Must NOT be guessed at — a WRONG code is worse than no code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"data": {"meta": {"customer_code": "PAGINATION-NOT-A-CUSTOMER"}}},
        {"data": {"items": [{"code": "FIRST-OF-A-COLLECTION"}]}},
        {"data": {"rows": [{"code": "FIRST-ROW"}]}},
        {"product": {"id": 99}},
        {"status": {"id": 7}},
    ],
    ids=["meta", "items", "rows", "unrelated-product", "unrelated-status"],
)
def test_an_unrelated_nested_value_is_NOT_taken_as_the_customer(body: object) -> None:
    """A wrong code shown to a customer is worse than a truthful "we could not read
    it" — so the search is deliberately narrower than "find any code anywhere"."""
    identifier, code = extract(body)
    assert (identifier, code) == (None, None), f"{body!r} -> {(identifier, code)!r}"


def test_the_search_is_depth_capped() -> None:
    """Deep enough for real envelopes, bounded so a hostile body stays cheap."""
    too_deep: object = {"code": "UNREACHABLE"}
    for _ in range(12):
        too_deep = {"data": too_deep}
    assert extract(too_deep) == (None, None)


def test_the_search_is_bounded_on_a_wide_body() -> None:
    """A body with thousands of wrapper keys must not blow up the walk."""
    wide = {f"k{i}": {"code": f"C{i}"} for i in range(5000)}
    identifier, code = extract(wide)
    assert isinstance(identifier, (str, type(None)))
    assert isinstance(code, (str, type(None)))


# ---------------------------------------------------------------------------
# Odd values
# ---------------------------------------------------------------------------


def test_a_numeric_id_of_zero_is_a_value() -> None:
    assert extract({"id": 0}) == ("0", None)


def test_a_numeric_code_of_zero_is_not_a_customer_code() -> None:
    """No customer is called "0"; accepting it would invent an identifier."""
    assert extract({"customer_code": 0}) == (None, None)


def test_a_whitespace_only_code_is_not_a_code() -> None:
    assert extract({"customer_code": "   "}) == (None, None)


def test_a_boolean_is_never_a_code_or_an_id() -> None:
    """`True` stringifies to "True" and would be a nonsense identifier."""
    assert extract({"code": True, "id": False}) == (None, None)


def test_a_non_object_payload_is_handled() -> None:
    for payload in ("a string", 42, None, []):
        assert extract(payload) == (None, None)
