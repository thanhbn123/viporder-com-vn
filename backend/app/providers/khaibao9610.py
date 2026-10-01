"""KHAIBAO9610 provider — CANDIDATE CONTRACT, NOT VERIFIED.

=====================================================================
 STATUS: unverified candidate. Do not treat as the production contract.
=====================================================================

The request/response shape below was reconstructed from the owner's own prior
integration work. It has **not** been confirmed against the upstream service,
and it is **not** reachable by default: the default mode is ``mock``.

Reaching a real customer system requires BOTH switches:

* ``KHAIBAO9610_MODE=http``
* ``KHAIBAO9610_ENABLE_REAL_CALLS=yes``

Missing either one raises :class:`ProviderConfigurationError` at construction
time. Refusing loudly is the point: a half-configured live adapter that
"mostly works" is how test data ends up in production.

Candidate contract (unverified)::

    POST {base}/register
    {"name": ..., "phone": ..., "email": ..., "password": ...,
     "confirmPassword": ..., "acceptTerms": true}

Cloudflare fronting the upstream rejects non-browser clients (Error 1010), so a
real browser ``User-Agent`` is mandatory.

SECURITY: the request body contains a plaintext password. This module never
logs it, never logs the body, and never includes the body in an exception
message.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import httpx

from .base import (
    ProviderConfigurationError,
    ProviderStatus,
    RegistrationRequest,
    RegistrationResult,
)

logger = logging.getLogger(__name__)

#: Candidate duplicate phrasing. Compared accent-stripped and lowercased, so
#: "Số điện thoại đã được đăng ký" and "da dang ky" both match.
DUPLICATE_PATTERN = re.compile(
    r"ton tai|already|taken|da duoc su dung|da dang ky|da co|exist",
    re.IGNORECASE,
)

MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 30.0


def strip_accents(text: str) -> str:
    """Return ``text`` with Vietnamese diacritics removed and lowercased."""
    if not text:
        return ""
    lowered = text.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def looks_like_duplicate(body: str) -> bool:
    """True when a rejection body says the phone is already registered."""
    return bool(DUPLICATE_PATTERN.search(strip_accents(body)))


class ViporderFrontendProvider:
    """Live adapter for the (unverified) KHAIBAO9610 frontend registration API."""

    name = "khaibao9610-frontend"

    def __init__(
        self,
        *,
        mode: str = "mock",
        enable_real_calls: bool = False,
        base_url: str,
        timeout_seconds: float = 10.0,
        user_agent: str,
        client: httpx.Client | None = None,
    ) -> None:
        if mode != "http":
            raise ProviderConfigurationError(
                "ViporderFrontendProvider requires KHAIBAO9610_MODE=http "
                f"(got {mode!r}). The default mode is 'mock' on purpose: the "
                "real upstream contract has not been supplied."
            )
        if not enable_real_calls:
            raise ProviderConfigurationError(
                "Refusing to construct the live provider: real calls are "
                "disabled. Set KHAIBAO9610_ENABLE_REAL_CALLS=yes together with "
                "KHAIBAO9610_MODE=http to make real registrations."
            )
        if not MIN_TIMEOUT_SECONDS <= float(timeout_seconds) <= MAX_TIMEOUT_SECONDS:
            raise ProviderConfigurationError(
                f"KHAIBAO9610_TIMEOUT_SECONDS must be between "
                f"{MIN_TIMEOUT_SECONDS:g} and {MAX_TIMEOUT_SECONDS:g}, "
                f"got {timeout_seconds!r}"
            )
        if not (base_url or "").strip():
            raise ProviderConfigurationError("KHAIBAO9610_BASE_URL must not be empty")
        if not (user_agent or "").strip():
            raise ProviderConfigurationError(
                "KHAIBAO9610_USER_AGENT must be set: the upstream is behind "
                "Cloudflare and rejects non-browser clients (Error 1010)."
            )

        self.mode = mode
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.user_agent = user_agent
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self.timeout_seconds)

    @classmethod
    def from_settings(cls, settings, client: httpx.Client | None = None):  # type: ignore[no-untyped-def]
        return cls(
            mode=settings.khaibao9610_mode,
            enable_real_calls=settings.khaibao9610_enable_real_calls,
            base_url=settings.khaibao9610_base_url,
            timeout_seconds=settings.khaibao9610_timeout_seconds,
            user_agent=settings.khaibao9610_user_agent,
            client=client,
        )

    # -- helpers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract(payload: object) -> tuple[str | None, str | None]:
        """Best-effort pull of (customer_id, customer_code) from a success body."""
        candidates: list[dict] = []
        if isinstance(payload, dict):
            candidates.append(payload)
            for key in ("data", "customer", "result"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)
        for source in candidates:
            code = None
            for key in ("customer_code", "customerCode", "code", "ma_khach_hang"):
                value = source.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    code = str(value).strip()
                    break
            identifier = None
            for key in ("customer_id", "customerId", "id", "user_id"):
                value = source.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    identifier = str(value).strip()
                    break
            if code or identifier:
                return identifier, code
        return None, None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- provider contract ---------------------------------------------------

    def register(self, request: RegistrationRequest) -> RegistrationResult:
        # NOTE: never log `payload` — it contains the plaintext password.
        payload = {
            "name": request.full_name,
            "phone": request.phone,
            "email": request.email,
            "password": request.password,
            "confirmPassword": request.password,
            "acceptTerms": True,
        }

        try:
            response = self._client.post(
                f"{self.base_url}/register",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "khaibao9610 register timed out after %ss (%s)",
                self.timeout_seconds,
                type(exc).__name__,
            )
            return RegistrationResult(
                status=ProviderStatus.UNAVAILABLE,
                message="The registration service did not respond in time.",
                http_status=None,
                retryable=True,
            )
        except httpx.HTTPError as exc:
            # Includes connection failures, DNS, TLS, protocol errors.
            logger.warning(
                "khaibao9610 register transport error (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return RegistrationResult(
                status=ProviderStatus.UNAVAILABLE,
                message="The registration service could not be reached.",
                http_status=None,
                retryable=True,
            )

        http_status = response.status_code
        # Body is used only for duplicate detection; it is never logged whole,
        # because an upstream error body can echo request fields.
        body_text = response.text or ""

        if 200 <= http_status < 300:
            identifier, code = self._extract(_safe_json(response))
            return RegistrationResult(
                status=ProviderStatus.SUCCESS,
                external_customer_id=identifier,
                external_customer_code=code,
                message="Registration completed.",
                http_status=http_status,
                retryable=False,
            )

        if http_status in (409, 422) and looks_like_duplicate(body_text):
            return RegistrationResult(
                status=ProviderStatus.DUPLICATE,
                message="This phone number is already registered.",
                http_status=http_status,
                retryable=False,
            )

        if http_status == 429 or http_status >= 500:
            logger.warning("khaibao9610 register unavailable (HTTP %s)", http_status)
            return RegistrationResult(
                status=ProviderStatus.UNAVAILABLE,
                message="The registration service is temporarily unavailable.",
                http_status=http_status,
                retryable=True,
            )

        logger.info("khaibao9610 rejected the registration (HTTP %s)", http_status)
        return RegistrationResult(
            status=ProviderStatus.INVALID,
            message="The registration service rejected these details.",
            http_status=http_status,
            retryable=False,
        )


def _safe_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except Exception:
        return None
