"""Public tracking routes.

Two lookups, both by keyword, both unauthenticated — they exist so a customer
holding a tracking code can see where their parcel is without signing in.

The response contract is IDENTICAL for both routes; only ``search_type`` differs::

    200 {"result": "found", "search_type": ..., "data": {...}}
    400 {"error": {"code": "INVALID_KEYWORD",      "message": "..."}}
    404 {"error": {"code": "NOT_FOUND",            "message": "..."}}
    502 {"error": {"code": "PROVIDER_ERROR",       "message": "..."}}
    503 {"error": {"code": "PROVIDER_UNAVAILABLE", "message": "..."}}

Errors are raised as :class:`~app.errors.ApiError` so they render through the
same envelope as every other route; the shape is not rebuilt here.

The two upstream envelopes differ — bare object versus ``{"data": {...}}`` — and
that difference is handled in :mod:`app.tracking`, once. This module never
inspects the body's shape.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..errors import (
    INVALID_KEYWORD,
    NOT_FOUND,
    PROVIDER_ERROR,
    PROVIDER_UNAVAILABLE,
    ApiError,
)
from ..providers.base import ProviderResponseError, ProviderUnavailableError
from ..tracking import (
    KIND_PACKAGE_SEALING,
    KIND_WAREHOUSE_IMPORT,
    InvalidKeywordError,
    normalize_package_sealing,
    normalize_warehouse_import,
    sanitise_keyword,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

# --- Customer-facing wording. VIETNAMESE, because the customer is. -----------
#
# Careful with the 404 text: it must never be taken from the upstream body. The
# upstream sends its own Vietnamese sentence, and echoing a third party's string
# straight into our response would hand it control of a message our customers
# read. These are ours, and they are per-endpoint so the customer is told which
# kind of code was not found.
MESSAGE_INVALID_KEYWORD = (
    "Mã tra cứu không hợp lệ. Vui lòng kiểm tra lại và chỉ nhập mã in trên phiếu."
)
MESSAGE_WAREHOUSE_IMPORT_NOT_FOUND = "Không tìm thấy mã vận đơn này. Vui lòng kiểm tra lại mã."
MESSAGE_PACKAGE_SEALING_NOT_FOUND = "Không tìm thấy mã đóng bao này. Vui lòng kiểm tra lại mã."
# 502 and 503 are intentionally distinct: one is "we are not answering correctly
# right now, tell support", the other is "the other side is down, retry later".
# Worded for a customer rather than an operator, because that is who reads it.
MESSAGE_PROVIDER_ERROR = (
    "Hệ thống tra cứu đang trả về dữ liệu không hợp lệ. Vui lòng thử lại sau "
    "hoặc liên hệ bộ phận hỗ trợ."
)
MESSAGE_PROVIDER_UNAVAILABLE = (
    "Hệ thống tra cứu tạm thời không phản hồi. Vui lòng thử lại sau ít phút."
)
#: The live tracking adapter is only constructed when the real-call switches are
#: on, so out of the box these routes have nobody to ask.
MESSAGE_TRACKING_DISABLED = "Chức năng tra cứu chưa được bật. Vui lòng liên hệ bộ phận hỗ trợ."


@router.get("/warehouse-imports/{keyword:path}")
def find_warehouse_import(keyword: str, request: Request) -> JSONResponse:
    """Look up one warehouse import (vận đơn) by tracking code.

    ``{keyword:path}`` rather than ``{keyword}``, and that is deliberate. A
    hostile keyword such as ``../../etc/passwd`` percent-encodes its slashes to
    ``%2F``, and the ASGI server decodes them back before routing. With a
    single-segment converter the route then does not match at all and the caller
    gets a routing **404** — indistinguishable from "this tracking code does not
    exist", which is exactly the wrong thing to tell someone probing. The path
    converter lets the whole value arrive here so it can be rejected explicitly
    with the documented **400 INVALID_KEYWORD**.

    Nothing is *served* by the extra width: every multi-segment value fails the
    keyword character set and is refused before any network call.
    """
    return _lookup(
        request,
        keyword,
        method_name="find_warehouse_import",
        search_type=KIND_WAREHOUSE_IMPORT,
        not_found_message=MESSAGE_WAREHOUSE_IMPORT_NOT_FOUND,
        normalize=normalize_warehouse_import,
    )


@router.get("/package-sealings/{keyword:path}")
def find_package_sealing(keyword: str, request: Request) -> JSONResponse:
    """Look up one package sealing (mã đóng bao) by sealing code.

    Same ``{keyword:path}`` reasoning as the route above.
    """
    return _lookup(
        request,
        keyword,
        method_name="find_package_sealing",
        search_type=KIND_PACKAGE_SEALING,
        not_found_message=MESSAGE_PACKAGE_SEALING_NOT_FOUND,
        normalize=normalize_package_sealing,
    )


def _lookup(
    request: Request,
    keyword: str,
    *,
    method_name: str,
    search_type: str,
    not_found_message: str,
    normalize,  # type: ignore[no-untyped-def]
) -> JSONResponse:
    """Shared body of both routes.

    The ordering is deliberate: **validate first**, so a hostile keyword costs a
    regex and never reaches the provider. Nothing below the validation step can
    run for an invalid keyword — there is no network call to count.

    The provider is looked up with ``getattr`` rather than assumed. Only the live
    adapter implements tracking, and it refuses to exist unless both real-call
    switches are set; in the default ``mock`` mode the configured provider has no
    tracking methods at all. Answering 503 is the honest result — the alternative
    would be to fabricate a provider, or to let this route reach the network in a
    configuration that was explicitly meant not to.
    """
    try:
        safe_keyword = sanitise_keyword(keyword)
    except InvalidKeywordError as exc:
        # The reason is logged; the customer gets the fixed sentence. The
        # offending value is NOT logged — it is attacker-controlled, and a log
        # line is not the place for it.
        logger.info("tracking keyword rejected search_type=%s reason=%s", search_type, exc)
        raise ApiError(400, INVALID_KEYWORD, MESSAGE_INVALID_KEYWORD) from None

    finder = getattr(request.app.state.provider, method_name, None)
    if finder is None:
        logger.warning(
            "tracking requested but the configured provider cannot track "
            "search_type=%s provider=%s",
            search_type,
            getattr(request.app.state.provider, "name", "unknown"),
        )
        raise ApiError(503, PROVIDER_UNAVAILABLE, MESSAGE_TRACKING_DISABLED)

    try:
        raw = finder(safe_keyword)
    except ProviderUnavailableError as exc:
        logger.warning("tracking provider unavailable search_type=%s error=%s", search_type, exc)
        raise ApiError(503, PROVIDER_UNAVAILABLE, MESSAGE_PROVIDER_UNAVAILABLE) from None
    except ProviderResponseError as exc:
        logger.error("tracking provider returned an unusable response: %s", exc)
        raise ApiError(502, PROVIDER_ERROR, MESSAGE_PROVIDER_ERROR) from None

    if raw is None:
        logger.info("tracking lookup found nothing search_type=%s", search_type)
        raise ApiError(404, NOT_FOUND, not_found_message)

    # `normalize` is the single place that knows the envelope difference. It is
    # written to tolerate any shape, so an unexpected body becomes nulls rather
    # than a 500.
    return JSONResponse(
        status_code=200,
        content={
            "result": "found",
            "search_type": search_type,
            "data": normalize(raw),
        },
    )
