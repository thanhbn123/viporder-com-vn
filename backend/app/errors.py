"""Error envelopes.

Every error a client can see has the same shape::

    {"error": {"code": "...", "message": "...", "fields": {...}}}

``code`` is stable and machine-readable; ``message`` is human-readable and
never contains a stack trace, a provider body, or user input echoed back.

Rule: no exception detail reaches the client. Details go to the server log,
tagged with the request's correlation id.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# --- Stable error codes -----------------------------------------------------
VALIDATION_ERROR = "VALIDATION_ERROR"
INVALID_REQUEST = "INVALID_REQUEST"
DUPLICATE_PHONE = "DUPLICATE_PHONE"
PROVIDER_INVALID = "PROVIDER_INVALID"
NOT_FOUND = "NOT_FOUND"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
INTERNAL_ERROR = "INTERNAL_ERROR"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"


class ApiError(Exception):
    """An error that maps directly onto the public envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        fields: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields or {}
        self.headers = headers or {}

    def envelope(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fields:
            error["fields"] = self.fields
        return {"error": error}


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    fields: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return JSONResponse(status_code=status_code, content={"error": error}, headers=headers or None)


def _field_name(location: tuple[Any, ...]) -> str:
    parts = [str(part) for part in location if part not in ("body", "query", "path")]
    return ".".join(parts) or "body"


def validation_fields(exc: RequestValidationError) -> dict[str, str]:
    """Flatten pydantic errors into ``{"full_name": "..."}``.

    Values under ``input``/``ctx`` are dropped on purpose. Pydantic echoes the
    offending input back, and for a password field that would put the plaintext
    password into the HTTP response *and* into the access log.
    """
    fields: dict[str, str] = {}
    for error in exc.errors():
        name = _field_name(tuple(error.get("loc", ())))
        message = str(error.get("msg", "Invalid value"))
        fields.setdefault(name, message)
    return fields


def sanitised_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """FastAPI-shaped error list with all input values removed."""
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors():
        cleaned.append(
            {
                "type": error.get("type", "value_error"),
                "loc": list(error.get("loc", ())),
                "msg": str(error.get("msg", "Invalid value")),
            }
        )
    return cleaned


def is_json_decode_error(exc: RequestValidationError) -> bool:
    return any(e.get("type") == "json_invalid" for e in exc.errors())


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content=exc.envelope(), headers=exc.headers or None
    )


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    if is_json_decode_error(exc):
        return error_response(
            400,
            INVALID_REQUEST,
            "The request body is not valid JSON.",
        )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": VALIDATION_ERROR,
                "message": "One or more fields are invalid.",
                "fields": validation_fields(exc),
            },
            # Standard FastAPI shape as well, minus every echoed input value.
            "detail": sanitised_errors(exc),
        },
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        400: INVALID_REQUEST,
        404: NOT_FOUND,
        405: METHOD_NOT_ALLOWED,
        413: PAYLOAD_TOO_LARGE,
        429: RATE_LIMITED,
    }.get(exc.status_code, "HTTP_ERROR")
    message = {
        404: "Not found.",
        405: "Method not allowed.",
        413: "Request body is too large.",
        429: "Too many requests.",
    }.get(exc.status_code, str(exc.detail))
    return error_response(exc.status_code, code, message)
