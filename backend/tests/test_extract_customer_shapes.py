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

    Both codes are AMBIGUOUS here, so pass 2 applies and the wrapper's complete pair
    wins over the top level's lonely code. Pairing two values that never arrived
    together would be a silent mismatch.
    """
    identifier, code = extract({"code": "SHALLOW", "data": {"code": "TT1", "id": "42"}})
    assert (identifier, code) == ("42", "TT1")


def test_an_UNAMBIGUOUS_key_wins_over_an_ambiguous_one() -> None:
    """`customer_code` can only mean a customer; `code` is also an envelope status.
    So the unambiguous key is taken even though it arrives without an id."""
    identifier, code = extract({"customer_code": "SHALLOW", "data": {"code": "TT1", "id": "42"}})
    assert code == "SHALLOW", f"the ambiguous key was preferred: {code!r}"


def test_the_shallowest_object_holding_BOTH_wins() -> None:
    """When the top level already has both, it is used and the walk stops."""
    identifier, code = extract(
        {"customer_code": "TOP", "id": "1", "data": {"code": "DEEP", "id": "2"}}
    )
    assert (identifier, code) == ("1", "TOP")


# ---------------------------------------------------------------------------
# The id can live in a DIFFERENT wrapper than the code (N-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"data": {"customer_code": "TT1"}, "customer": {"customer_id": "9"}},
            ("9", "TT1"),
        ),
        (
            {"customer_code": "TT1", "customer_data": {"user_id": "10"}},
            ("10", "TT1"),
        ),
        (
            {"result": {"customer_code": "TT1"}, "account": {"customerId": "11"}},
            ("11", "TT1"),
        ),
        (
            {"code": "LONELY", "customer": {"customer_id": "12"}},
            ("12", "LONELY"),
        ),
        (
            {"data": {"code": "TT1"}, "customer": {"customer_id": "13"}},
            ("13", "TT1"),
        ),
    ],
    ids=[
        "data-code_customer-customer_id",
        "top-code_customer_data-user_id",
        "result-code_account-camel-id",
        "ambiguous-top-code_customer-customer_id",
        "ambiguous-data-code_customer-customer_id",
    ],
)
def test_an_id_in_a_SIBLING_wrapper_is_paired_with_the_code(
    body: object, expected: tuple[str, str]
) -> None:
    """The id scan is INDEPENDENT of the code scan, then the two are joined.

    A code on one node and a real ``customer_id`` on a SIBLING node used to return
    ``(None, code)``: the id was never looked at, because its node held no code. The
    id that really was present in the body was silently dropped — this pins that it
    is now paired with the code instead.
    """
    assert extract(body) == expected, f"{body!r} -> {extract(body)!r}"


def test_a_same_object_pair_beats_a_shallower_sibling_id() -> None:
    """Independence must not undo the pairing rule.

    The top level carries an id and nothing else, while ``data`` carries a code and an
    id TOGETHER. The complete pair is the stronger evidence and must win, even though
    the lonely id is shallower.
    """
    identifier, code = extract({"id": "99", "data": {"customer_code": "TT1", "id": "42"}})
    assert (identifier, code) == ("42", "TT1")


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


# ---------------------------------------------------------------------------
# THE ADVERSARIAL FINDINGS — an envelope's own status must never be the code
# ---------------------------------------------------------------------------


def test_the_laravel_envelope_yields_the_REAL_code_not_the_status() -> None:
    """THE BUG AN ADVERSARIAL REVIEW FOUND, and it was severe.

    `{"code": 200, "message": "success", "data": {"customer_code": "TT00123"}}` is the
    most common Laravel-shaped response. The old extractor returned `(None, "200")`:
    the envelope's own status was handed to the customer as their code — a number that
    signs in nowhere — while the REAL code inside `data` was discarded. The lead then
    went `REGISTERED`, so the partial unique index blocked that phone permanently and
    the retry route answered "already registered".
    """
    identifier, code = extract(
        {"code": 200, "message": "success", "data": {"customer_code": "TT00123"}}
    )
    assert code == "TT00123", f"the envelope status was taken as the code: {code!r}"


@pytest.mark.parametrize(
    "body",
    [{"code": 200}, {"code": "0"}, {"code": 201, "message": "created"}],
    ids=["int-200", "string-0", "created-201"],
)
def test_an_envelope_status_alone_is_NOT_a_success(body: object) -> None:
    """Nothing but a status means the registration identified nobody, and the honest
    answer is UNUSABLE_RESPONSE — with the body kept for an operator to read."""
    assert extract(body) == (None, None), f"{body!r} -> {extract(body)!r}"


def test_an_all_digit_code_under_the_ambiguous_key_is_refused() -> None:
    """A real all-digit customer code would be refused, and that is the deliberate
    direction: no code is truthful; a wrong one is not."""
    assert extract({"code": "12345678"}) == (None, None)


def test_an_all_digit_code_under_an_UNAMBIGUOUS_key_is_accepted() -> None:
    """Because `customer_code: 12345` can only mean a customer."""
    assert extract({"customer_code": 12345}) == (None, "12345")
