"""Request/response schemas.

The password field is a :class:`~pydantic.SecretStr`. That is a deliberate
choice: ``repr()``, ``str()``, ``model_dump()`` and tracebacks of this model all
render ``**********`` instead of the value, so the password cannot escape into a
log or an error message by accident. The one place it is unwrapped is the call
to the provider.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

from .phone import InvalidPhoneError, normalise_phone

ServiceInterest = Literal["transport", "official_import", "customs", "order"]

MAX_ATTRIBUTION_LENGTH = 300
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200


class Attribution(BaseModel):
    """Optional marketing attribution. Every sub-field is optional."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    utm_source: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None
    utm_medium: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None
    utm_campaign: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None
    utm_content: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None
    utm_term: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None
    landing_page: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None
    referrer: Annotated[str | None, Field(max_length=MAX_ATTRIBUTION_LENGTH)] = None

    @field_validator(
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "landing_page",
        "referrer",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("landing_page")
    @classmethod
    def _absolute_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("landing_page must be an absolute http(s) URL")
        return value


class RegistrationCreate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    full_name: Annotated[str, Field(min_length=2, max_length=120)]
    phone: str
    password: SecretStr
    email: EmailStr | None = None
    province: Annotated[str | None, Field(max_length=120)] = None
    service_interest: ServiceInterest | None = None
    consent: bool
    attribution: Attribution | None = None

    @field_validator("email", "province", "service_interest", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("full_name")
    @classmethod
    def _name_length(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 2 or len(trimmed) > 120:
            raise ValueError("full_name must be between 2 and 120 characters")
        return trimmed

    @field_validator("phone")
    @classmethod
    def _normalise_phone(cls, value: str) -> str:
        try:
            return normalise_phone(value)
        except InvalidPhoneError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("password")
    @classmethod
    def _password_length(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        if len(raw) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
        return value

    @field_validator("consent")
    @classmethod
    def _consent_required(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("consent must be true to register")
        return value


# --- Response bodies (plain dicts are built in the router; these document the
# --- contract and are used for OpenAPI).
class RegistrationAcceptedResponse(BaseModel):
    lead_id: str
    registration_status: str
    external_customer_id: str | None = None
    external_customer_code: str | None = None
    message: str
    login_url: str


class RegistrationPendingResponse(RegistrationAcceptedResponse):
    tracking_token: str


class LeadStatusResponse(BaseModel):
    lead_id: str
    registration_status: str
    external_customer_code: str | None = None
    created_at: str
    updated_at: str
