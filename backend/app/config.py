"""Runtime configuration for the VIPORDER backend.

Everything is read from the environment. Nothing here is a credential, and no
secret ever gets a default value: an unset secret must disable the feature that
needs it, never fall back to something guessable.

Provider safety (hard constraint): ``KHAIBAO9610_MODE`` defaults to ``mock``.
The real upstream registration contract has not been supplied, so the live
adapter must be opted into *twice* — once by selecting ``http`` mode and once by
setting ``KHAIBAO9610_ENABLE_REAL_CALLS=yes``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Server-side constants --------------------------------------------------
# The customer portal is a fixed, server-side destination. It is intentionally
# NOT configurable and NEVER derived from user input: an attacker-supplied
# redirect target is the classic open-redirect bug.
LOGIN_URL = "https://khachhang.viporder.com.vn"
SERVICE_NAME = "viporder-web"
DEFAULT_SERVICE_VERSION = "0.2.0"

PROVIDER_MODES = ("mock", "http")
MOCK_BEHAVIOURS = ("success", "duplicate", "invalid", "unavailable", "timeout", "error")

# Cloudflare Error 1010 rejects non-browser User-Agents, so the live adapter
# must present a real browser UA. This is wiring, not a credential.
DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Candidate upstream contract (UNVERIFIED — see providers/khaibao9610.py).
DEFAULT_KHAIBAO9610_BASE_URL = "https://apiviporder.com/frontend/v1"

# Upstream paths. These are settings rather than literals because the two halves
# of this integration were measured at different times and the paths are the part
# most likely to move: a path change then costs one environment variable instead
# of a release. ``{keyword}`` is substituted with a URL-encoded path segment —
# exactly one placeholder per template, enforced by the validator below.
#
# The two tracking paths are NOT interchangeable. Measured against the live
# service: ``/warehouse-imports/{keyword}`` answers with a BARE object and
# ``/package-sealings/{keyword}`` answers with ``{"data": {...}}``. See
# app/tracking.py.
DEFAULT_KHAIBAO9610_REGISTER_PATH = "/register"
DEFAULT_KHAIBAO9610_WAREHOUSE_IMPORT_PATH = "/warehouse-imports/{keyword}"
DEFAULT_KHAIBAO9610_PACKAGE_SEALING_PATH = "/package-sealings/{keyword}"

#: Cap on a tracking response body. The upstream is a third party we do not
#: control; without a cap, one oversized reply is enough to exhaust the worker's
#: memory. 2 MiB is far above the largest measured response and far below
#: anything that would matter.
DEFAULT_KHAIBAO9610_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

#: Ceiling on the *connect* half of the timeout. A connect that has not
#: completed in a few seconds is a network problem, not a slow query, and making
#: the customer wait the full read timeout for it helps nobody.
CONNECT_TIMEOUT_CEILING_SECONDS = 5.0

# Which consent wording the customer agreed to, stored on every lead.
# Bump this whenever the on-page wording changes: the version is the evidence,
# and a legal question about a specific customer is answered by knowing which
# sentence they actually saw. Keep it short — the column is 32 characters.
CONSENT_VERSION = "2026-02-v1"


class Settings(BaseSettings):
    """Environment-backed settings.

    Boolean keys accept ``yes``/``no`` (the documented spelling) as well as
    ``true``/``false``/``1``/``0``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # `.env.example` documents every key with an EMPTY value, so copying it
        # verbatim must be harmless: an empty environment variable means "unset"
        # and the documented default applies.
        env_ignore_empty=True,
    )

    # --- Service ------------------------------------------------------------
    app_env: str = "development"
    service_name: str = SERVICE_NAME
    app_version: str = DEFAULT_SERVICE_VERSION

    # --- Persistence --------------------------------------------------------
    # Dev default lives under var/, which is git-ignored: a lead database must
    # never be committable. Production uses postgresql+psycopg://...
    database_url: str = "sqlite:///./var/viporder.db"
    # Convenience for `uvicorn app.main:app` with no setup step. Deployments
    # where Alembic owns the schema should set AUTO_CREATE_SCHEMA=no; the
    # migration is written to be a no-op when the tables already exist, so the
    # two paths never fight.
    auto_create_schema: bool = True

    # --- Registration provider ---------------------------------------------
    khaibao9610_mode: Literal["mock", "http"] = "mock"
    khaibao9610_base_url: str = DEFAULT_KHAIBAO9610_BASE_URL
    khaibao9610_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    khaibao9610_enable_real_calls: bool = False
    khaibao9610_user_agent: str = DEFAULT_BROWSER_USER_AGENT

    # --- Upstream paths and response cap ------------------------------------
    khaibao9610_register_path: str = DEFAULT_KHAIBAO9610_REGISTER_PATH
    khaibao9610_warehouse_import_path: str = DEFAULT_KHAIBAO9610_WAREHOUSE_IMPORT_PATH
    khaibao9610_package_sealing_path: str = DEFAULT_KHAIBAO9610_PACKAGE_SEALING_PATH
    khaibao9610_max_response_bytes: int = Field(
        default=DEFAULT_KHAIBAO9610_MAX_RESPONSE_BYTES, ge=1024
    )

    # --- Mock provider ------------------------------------------------------
    mock_provider_behaviour: Literal[
        "success", "duplicate", "invalid", "unavailable", "timeout", "error"
    ] = "success"

    # --- Admin retry --------------------------------------------------------
    # Empty means the admin route does not exist at all (404), not "open".
    admin_api_token: str = ""

    # --- API docs -----------------------------------------------------------
    # None = decide from APP_ENV: docs are on outside production and off inside
    # it. /docs and /openapi.json enumerate every route, parameter and error
    # code — a map for an attacker, and nothing the public site needs.
    enable_api_docs: bool | None = None

    # --- Security -----------------------------------------------------------
    max_request_bytes: int = Field(default=64 * 1024, ge=1024)
    rate_limit_enabled: bool = True
    rate_limit_attempts: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=600, ge=1)
    # How long an unanswered phone claim is believed before another attempt may
    # reclaim it. This is the crash-recovery bound: a process killed mid-attempt
    # would otherwise block that phone forever. It must comfortably exceed the
    # provider timeout, or a slow provider would let a second attempt through and
    # reintroduce the duplicate call the claim exists to prevent.
    phone_claim_ttl_seconds: int = Field(default=120, ge=30)
    trust_proxy_headers: bool = False
    # Comma-separated origins. Empty = CORS disabled entirely (same-origin).
    cors_allow_origins: str = ""

    @field_validator("admin_api_token", "khaibao9610_base_url", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("khaibao9610_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator(
        "khaibao9610_register_path",
        "khaibao9610_warehouse_import_path",
        "khaibao9610_package_sealing_path",
        mode="before",
    )
    @classmethod
    def _strip_path(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "khaibao9610_register_path",
        "khaibao9610_warehouse_import_path",
        "khaibao9610_package_sealing_path",
    )
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        """A path template must be absolute.

        Without this, ``KHAIBAO9610_WAREHOUSE_IMPORT_PATH=warehouse-imports/x``
        concatenates onto the base URL and silently produces
        ``.../v1warehouse-imports/x`` — a 404 the operator would read as
        "the tracking code does not exist". A wrong path and a wrong code are
        very different things to tell a customer, so the mistake is refused at
        startup instead.
        """
        if not value.startswith("/"):
            raise ValueError(f"must start with '/', got {value!r}")
        return value

    @field_validator(
        "khaibao9610_warehouse_import_path",
        "khaibao9610_package_sealing_path",
    )
    @classmethod
    def _one_keyword_placeholder(cls, value: str) -> str:
        """Exactly one ``{keyword}``: no more (ambiguous), no fewer (dead path).

        A template with no placeholder would query a fixed URL and return
        whichever record that URL happens to describe — a lookup that answers
        with the wrong parcel would be worse than one that fails.
        """
        count = value.count("{keyword}")
        if count != 1:
            raise ValueError(f"must contain exactly one '{{keyword}}', found {count} in {value!r}")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_api_token)

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def api_docs_enabled(self) -> bool:
        """Whether /docs and /openapi.json are served.

        Off in production by default; an explicit ENABLE_API_DOCS wins either
        way, so an operator who really needs the docs in production can say so.
        """
        if self.enable_api_docs is not None:
            return self.enable_api_docs
        return not self.is_production

    @property
    def login_url(self) -> str:
        return LOGIN_URL


def get_settings(**overrides: object) -> Settings:
    """Build settings from the environment, with optional explicit overrides."""
    return Settings(**overrides)  # type: ignore[arg-type]
