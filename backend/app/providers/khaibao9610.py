"""KHAIBAO9610 provider — MIXED VERIFICATION STATE. Read which half you are in.

=============================================================================
  REGISTRATION (POST {base}/register): CANDIDATE CONTRACT, NOT VERIFIED.
  TRACKING (GET warehouse-imports / package-sealings): MEASURED, see below.
=============================================================================

REGISTRATION — unverified. The request/response shape was reconstructed from the
owner's own prior integration work. It has **not** been confirmed against the
upstream service, and it is **not** reachable by default: the default mode is
``mock``. The success and duplicate schemas are UNKNOWN, so the response parsing
below stays deliberately defensive and reads nothing it was not told to read.

TRACKING — measured live (see app/tracking.py for the full note). Two endpoints,
and **their envelopes differ**, which is the single most important fact about
them:

* ``GET /warehouse-imports/{keyword}`` → a BARE object.
* ``GET /package-sealings/{keyword}`` → ``{"data": {...}}`` (wrapped).

A parser that assumes one shape fails on the other. These two methods return the
raw parsed body and do not unwrap anything: unwrapping is a normalisation
concern, and putting it here is exactly how the two shapes get conflated.

Reaching a real customer system requires ``KHAIBAO9610_MODE=http`` **plus at
least one of the two capability switches** — they are separate on purpose:

* ``KHAIBAO9610_ENABLE_REAL_CALLS=yes`` — live tracking lookups (reads)
* ``KHAIBAO9610_ENABLE_REAL_REGISTRATION=yes`` — live customer registration (writes)

With both disabled, construction raises :class:`ProviderConfigurationError`.
With ONE enabled, the adapter exists and the OTHER capability refuses at the call
site (``track()`` checks the read switch, ``register()`` the write switch), so
live tracking can be turned on without arming live registration. That split is
not decoration: while the two capabilities shared one switch, enabling tracking
for acceptance also enabled registration writes, and an acceptance test created
an account on the provider's production API. See
``khaibao9610_enable_real_registration`` in ``app/config.py``.

Refusing loudly is still the point: a half-configured live adapter that "mostly
works" is how test data ends up in production.

SECURITY: the registration request body contains a plaintext password and its
confirmation. This module never logs either, never logs the body, and never
includes the body in an exception message.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from urllib.parse import quote

import httpx

from ..config import CONNECT_TIMEOUT_CEILING_SECONDS
from .base import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderStatus,
    ProviderUnavailableError,
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


#: How much of an unparseable upstream body is kept. Enough to see the KEY NAMES —
#: which is the diagnostic question — and not enough to become a data dump.
DIAGNOSTIC_EXCERPT_CHARS = 400


def _secret_variants(secret: str) -> set[str]:
    """Every spelling a secret can take once it has been through JSON or unicode.

    WHY THIS IS NOT JUST `str.replace`. An upstream that echoes the request emits the
    ESCAPED form of the values, not the raw bytes. Measured:

        body  '{"error":"password pa\\"ss\\\\word rejected"}'
        secret 'pa"ss\\word'
        plain replace -> NOTHING redacted

    A password containing a quote, a backslash, a newline or **any non-ASCII
    character** — which is most Vietnamese passwords — therefore survived redaction
    and sat in `leads.last_error_message`, recoverable with one `json.loads`. The
    byte-scan leakage test could not see it, because the stored bytes differ from the
    plaintext.

    So every escaped spelling is redacted too: JSON-escaped, unicode-escaped, and both
    unicode normalisations.
    """
    import json as _json
    import unicodedata

    forms = {secret}
    forms.add(_json.dumps(secret, ensure_ascii=False)[1:-1])  # JSON body escaping
    forms.add(_json.dumps(secret, ensure_ascii=True)[1:-1])  # \uXXXX escaping
    forms.add(secret.encode("unicode_escape").decode("ascii"))
    for form in list(forms):
        forms.add(unicodedata.normalize("NFC", form))
        forms.add(unicodedata.normalize("NFD", form))
    forms.discard("")
    return forms


def _redacted_excerpt(body: str, *secrets: str | None) -> str:
    """A short, REDACTED copy of an upstream body, safe to persist.

    Removing the secrets is not optional: an upstream error body can echo the
    request, and this text is written into the database and shown to operators.
    Redaction covers every escaped spelling — see `_secret_variants`.

    Longest-first so a secret that contains another secret cannot leave a fragment
    behind.
    """
    text = (body or "").strip()
    if not text:
        return ""
    variants: set[str] = set()
    for secret in secrets:
        if secret:
            variants |= _secret_variants(secret)
    for variant in sorted(variants, key=len, reverse=True):
        text = text.replace(variant, "<REDACTED>")
    if len(text) > DIAGNOSTIC_EXCERPT_CHARS:
        text = text[:DIAGNOSTIC_EXCERPT_CHARS] + "…<truncated>"
    return text


class ViporderFrontendProvider:
    """Live adapter for the (unverified) KHAIBAO9610 frontend registration API."""

    name = "khaibao9610-frontend"

    def __init__(
        self,
        *,
        mode: str = "mock",
        enable_real_calls: bool = False,
        enable_real_registration: bool = False,
        base_url: str,
        timeout_seconds: float = 10.0,
        user_agent: str,
        register_path: str = "/register",
        warehouse_import_path: str = "/warehouse-imports/{keyword}",
        package_sealing_path: str = "/package-sealings/{keyword}",
        max_response_bytes: int = 2 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        if mode != "http":
            raise ProviderConfigurationError(
                "ViporderFrontendProvider requires KHAIBAO9610_MODE=http "
                f"(got {mode!r}). The default mode is 'mock' on purpose: the "
                "real upstream contract has not been supplied."
            )
        if not (enable_real_calls or enable_real_registration):
            raise ProviderConfigurationError(
                "Refusing to construct the live provider: BOTH capabilities are "
                "disabled. Set KHAIBAO9610_MODE=http plus at least one of "
                "KHAIBAO9610_ENABLE_REAL_CALLS=yes (tracking lookups) or "
                "KHAIBAO9610_ENABLE_REAL_REGISTRATION=yes (customer registration)."
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
        for label, path in (
            ("REGISTER_PATH", register_path),
            ("WAREHOUSE_IMPORT_PATH", warehouse_import_path),
            ("PACKAGE_SEALING_PATH", package_sealing_path),
        ):
            # Settings validate these too. Repeated here because the provider is
            # constructed directly in tests and by anything that predates the
            # settings field, and a relative path silently concatenates onto the
            # base URL into a 404 that reads like "no such tracking code".
            if not (path or "").startswith("/"):
                raise ProviderConfigurationError(f"KHAIBAO9610_{label} must start with '/'")
        for label, path in (
            ("WAREHOUSE_IMPORT_PATH", warehouse_import_path),
            ("PACKAGE_SEALING_PATH", package_sealing_path),
        ):
            if path.count("{keyword}") != 1:
                raise ProviderConfigurationError(
                    f"KHAIBAO9610_{label} must contain exactly one '{{keyword}}'"
                )
        if int(max_response_bytes) < 1024:
            raise ProviderConfigurationError(
                f"KHAIBAO9610_MAX_RESPONSE_BYTES must be at least 1024, got {max_response_bytes!r}"
            )

        self.mode = mode
        # Stored, not merely validated. The constructor previously checked the flag
        # and discarded it, so `register()` could not tell whether writes were
        # allowed — which is how one switch came to gate both capabilities.
        self.enable_real_calls = enable_real_calls
        self.enable_real_registration = enable_real_registration
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.user_agent = user_agent
        self.register_path = register_path
        self.warehouse_import_path = warehouse_import_path
        self.package_sealing_path = package_sealing_path
        self.max_response_bytes = int(max_response_bytes)
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self.timeout_seconds)

    @classmethod
    def from_settings(cls, settings, client: httpx.Client | None = None):  # type: ignore[no-untyped-def]
        return cls(
            mode=settings.khaibao9610_mode,
            enable_real_calls=settings.khaibao9610_enable_real_calls,
            enable_real_registration=settings.khaibao9610_enable_real_registration,
            base_url=settings.khaibao9610_base_url,
            timeout_seconds=settings.khaibao9610_timeout_seconds,
            user_agent=settings.khaibao9610_user_agent,
            register_path=settings.khaibao9610_register_path,
            warehouse_import_path=settings.khaibao9610_warehouse_import_path,
            package_sealing_path=settings.khaibao9610_package_sealing_path,
            max_response_bytes=settings.khaibao9610_max_response_bytes,
            client=client,
        )

    # -- helpers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_headers(self) -> dict[str, str]:
        """Headers for a GET.

        Deliberately no ``Content-Type``: there is no body to describe, and a
        content type on a bodyless GET is the kind of thing a WAF scores.
        """
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def _timeout(self) -> httpx.Timeout:
        """A connect timeout separate from the read timeout.

        With one number for both, a host that accepts the TCP connection and then
        never answers and a host that never answers the handshake are
        indistinguishable — and the connect half is the one worth failing fast on.
        """
        return httpx.Timeout(
            self.timeout_seconds,
            connect=min(CONNECT_TIMEOUT_CEILING_SECONDS, self.timeout_seconds),
        )

    def _tracking_get(self, template: str, keyword: str) -> dict | None:
        """One GET against a ``{keyword}`` path template.

        Returns the raw parsed JSON object, or ``None`` on HTTP 404 — the
        measured "this code does not exist" answer, which is a *result*, not an
        error.

        Raises:
            ProviderUnavailableError: timeout, transport failure, or upstream 429.
            ProviderResponseError: upstream 5xx, or a body that is not a JSON
                object.

        NO RETRIES, on purpose. The upstream is a third party, and a retried GET
        is only safe if the request is idempotent *and* that is proven. The
        owner's measurement did not test what these endpoints do on repeat, so no
        retry is added — a single attempt that fails is reported as a failure.
        ``httpx.Client`` itself defaults to ``HTTPTransport(retries=0)``, so
        there is no hidden retry under this either.

        The keyword is percent-encoded with ``safe=""``, which encodes ``/``,
        ``?``, ``#``, ``&`` and the pipe that real codes contain. Nothing a caller
        can put in a keyword is therefore able to add a path segment, start a
        query, or introduce a fragment.
        """
        segment = quote(keyword, safe="")
        url = f"{self.base_url}{template.replace('{keyword}', segment)}"
        try:
            with self._client.stream(
                "GET", url, headers=self._get_headers(), timeout=self._timeout()
            ) as response:
                status = response.status_code
                if status == 404:
                    return None
                if status == 429:
                    raise ProviderUnavailableError(
                        "The tracking service is rate-limiting this client (HTTP 429)."
                    )
                if status >= 400:
                    # Everything left that is >= 400: the other 4xx (401, 403, 405,
                    # 400, ...) and every 5xx. The 4xx cases mean our idea of this
                    # endpoint is wrong — the measurement said the keyword form
                    # needs no auth, so a 401 here is a contract change, not a
                    # customer problem — and a 5xx means the upstream is broken.
                    # Both are "the provider answered unusably", which is the 502.
                    raise ProviderResponseError(
                        f"The tracking service answered HTTP {status} for {template}."
                    )
                # NOTE: a 3xx reaches here rather than being followed. Redirects
                # are not enabled on the client (`follow_redirects` defaults to
                # False), which is deliberate: following them would let the
                # upstream send this service to a host of its choosing, and the
                # measured endpoints answer 200 directly. A redirect therefore
                # falls through to the JSON parse and surfaces as a 502.
                body = self._read_capped(response)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("The tracking service did not respond in time.") from exc
        except httpx.HTTPError as exc:
            # Includes connection failures, DNS, TLS, protocol errors.
            raise ProviderUnavailableError("The tracking service could not be reached.") from exc

        try:
            payload = json.loads(body)
        except ValueError as exc:
            # The content type is deliberately never inspected: tolerance for a
            # wrong or missing charset header is the point, and the parse result
            # is the only thing that actually matters.
            raise ProviderResponseError(
                "The tracking service returned a body that is not JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderResponseError("The tracking service returned JSON that is not an object.")
        return payload

    def _read_capped(self, response: httpx.Response) -> bytes:
        """Read a response body, refusing to exceed :attr:`max_response_bytes`.

        Read in chunks rather than with ``response.read()`` so the cap is applied
        *while* the body arrives. Buffering first and measuring afterwards would
        mean the memory is already spent, which defeats the cap entirely.
        """
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > self.max_response_bytes:
                raise ProviderResponseError(
                    f"The tracking service returned more than {self.max_response_bytes} bytes."
                )
        return bytes(body)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    #: Keys an upstream wraps its payload in. The search descends through THESE and
    #: nothing else, so a nested `id` belonging to a product or a status object
    #: cannot be mistaken for the customer's.
    _WRAPPER_KEYS = (
        "data",
        "result",
        "payload",
        "body",
        "response",
        "customer",
        "customer_info",
        "customer_data",
        "user",
        "user_info",
        "account",
        # Singular, payload-bearing wrappers common to Laravel-style APIs.
        # Deliberately NOT `meta` (pagination), nor plural collection keys
        # (`items`, `rows`) — taking element zero of a collection is how a WRONG
        # code gets shown to a customer, and a wrong code is worse than no code:
        # no code is truthful and the evidence is now kept for the operator.
        "attributes",
        "record",
        "entry",
        "item",
    )

    #: Keys that can ONLY mean a customer code. Anything found here is trusted.
    _CODE_KEYS = ("customer_code", "customerCode", "ma_khach_hang", "ma_kh")

    #: Keys that USUALLY mean a customer code but are also used for an envelope's own
    #: status. `{"code": 200, "message": "success", "data": {...}}` is the most common
    #: Laravel-shaped response, and taking that `200` as the customer's code is how a
    #: customer is handed a number that signs in nowhere — while the real code inside
    #: `data` is discarded. So a value here is accepted only when it does NOT look
    #: like a status: a bare number is a status, not a customer.
    _AMBIGUOUS_CODE_KEYS = ("code",)
    _ID_KEYS = ("customer_id", "customerId", "id", "user_id", "userId")

    @classmethod
    def _extract(cls, payload: object) -> tuple[str | None, str | None]:
        """Best-effort pull of (customer_id, customer_code) from a success body.

        SEARCHES THROUGH WRAPPERS, TO A BOUNDED DEPTH, IN TWO PASSES.

        Pass 1 looks only under keys that can ONLY mean a customer code
        (``customer_code``, ``customerCode``, ``ma_khach_hang``, ``ma_kh``).

        Pass 2 falls back to the ambiguous ``code`` key — but only for values that do
        not look like an envelope status.

        THE BUG THIS SHAPE FIXES. A response like::

            {"code": 200, "message": "success", "data": {"customer_code": "TT00123"}}

        used to return ``(None, "200")``: the envelope's own status number was taken
        as the customer's code and the REAL code inside ``data`` was thrown away. The
        customer was then told the registration succeeded and handed a code that
        signs in nowhere, while the lead went ``REGISTERED`` — so the partial unique
        index blocked that phone permanently and the retry route said "already
        registered". A wrong code is far worse than no code, because no code is
        truthful and the evidence is kept for an operator to read.
        """
        if not isinstance(payload, (dict, list)):
            return None, None

        nodes = cls._walk(payload)

        for picker in (cls._pick, cls._pick_ambiguous):
            first_code: str | None = None
            first_id: str | None = None
            for node in nodes:
                code = picker(
                    node, cls._CODE_KEYS if picker is cls._pick else cls._AMBIGUOUS_CODE_KEYS
                )
                if code is None:
                    continue
                identifier = cls._pick(node, cls._ID_KEYS)
                if identifier is not None:
                    return identifier, code
                if first_code is None:
                    first_code, first_id = code, identifier
            if first_code is not None:
                return first_id, first_code

        # No code anywhere. An identifier alone is still a usable success.
        for node in nodes:
            identifier = cls._pick(node, cls._ID_KEYS)
            if identifier is not None:
                return identifier, None
        return None, None

    @classmethod
    def _walk(cls, payload: object) -> list[dict]:
        """Breadth-first collection of candidate objects, bounded.

        Descends only through named wrapper keys, so an unrelated nested ``id``
        cannot be mistaken for the customer's.
        """
        nodes: list[dict] = []
        seen = 0
        queue: list[tuple[object, int]] = [(payload, 0)]
        while queue and seen < cls._MAX_NODES:
            node, depth = queue.pop(0)
            if isinstance(node, list):
                for item in node[:20]:
                    if isinstance(item, (dict, list)):
                        queue.append((item, depth))
                continue
            if not isinstance(node, dict):
                continue
            seen += 1
            nodes.append(node)
            if depth >= cls._MAX_DEPTH:
                continue
            for key in cls._WRAPPER_KEYS:
                nested = node.get(key)
                if isinstance(nested, (dict, list)):
                    queue.append((nested, depth + 1))
        return nodes

    @staticmethod
    def _pick_ambiguous(source: dict, keys: tuple[str, ...]) -> str | None:
        """Like `_pick`, but REFUSES a value that looks like an envelope status.

        A bare number — `200`, `"0"`, `201` — is a status code, not a customer. So is
        a digit-only string. A real customer code that happens to be all digits would
        be refused here, and that is the deliberate direction: the result is
        `UNUSABLE_RESPONSE`, the customer is told the truth, and the upstream body is
        preserved for an operator. Handing over a wrong code is worse.
        """
        value = ViporderFrontendProvider._pick(source, keys)
        if value is None:
            return None
        if value.isdigit():
            return None
        return value

    @staticmethod
    def _pick(source: dict, keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                # A numeric id of 0 is a value. A numeric `code` is handled by
                # `_pick_ambiguous` and refused there; under the UNAMBIGUOUS keys a
                # number is still accepted, because `customer_code: 12345` can only
                # mean a customer.
                if value == 0 and "code" in key.lower():
                    continue
                return str(value)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
        return None

    #: Bounds for the search. Wide enough for any sane envelope, small enough that
    #: a hostile or malformed body cannot make this expensive.
    _MAX_DEPTH = 4
    _MAX_NODES = 60

    # -- tracking contract ---------------------------------------------------
    #
    # Measured live; see the module docstring and app/tracking.py.
    #
    # These return the RAW parsed body and deliberately do NOT unwrap the
    # envelope. `/warehouse-imports/{keyword}` answers with a bare object and
    # `/package-sealings/{keyword}` answers with `{"data": {...}}`. Unwrapping
    # here would mean guessing which shape arrived, and guessing is how the two
    # get conflated. Normalisation owns that decision, in one place.

    def find_warehouse_import(self, keyword: str) -> dict | None:
        # THE READ SWITCH. Separate from the write switch so that a tracking
        # test can never create a customer (see the class docstring).
        if not self.enable_real_calls:
            raise ProviderUnavailableError(
                "Real tracking lookups are disabled. Set "
                "KHAIBAO9610_ENABLE_REAL_CALLS=yes to allow them."
            )
        """Look up one warehouse import by tracking code.

        ``None`` means HTTP 404 — the measured "Mã vận đơn không tồn tại".
        """
        return self._tracking_get(self.warehouse_import_path, keyword)

    def find_package_sealing(self, keyword: str) -> dict | None:
        # THE READ SWITCH. Separate from the write switch so that a tracking
        # test can never create a customer (see the class docstring).
        if not self.enable_real_calls:
            raise ProviderUnavailableError(
                "Real tracking lookups are disabled. Set "
                "KHAIBAO9610_ENABLE_REAL_CALLS=yes to allow them."
            )
        """Look up one package sealing by sealing code.

        ``None`` means HTTP 404. On success the caller gets the FULL envelope,
        ``{"data": {...}}``, not the inner object.
        """
        return self._tracking_get(self.package_sealing_path, keyword)

    # -- provider contract ---------------------------------------------------

    def register(self, request: RegistrationRequest) -> RegistrationResult:
        # THE WRITE SWITCH. Reads and writes are gated separately so that enabling
        # live tracking lookups cannot, by itself, allow a customer to be created on
        # the provider's production system. That is exactly what happened once.
        if not self.enable_real_registration:
            raise ProviderUnavailableError(
                "Real registration is disabled. Set "
                "KHAIBAO9610_ENABLE_REAL_REGISTRATION=yes to allow it — "
                "KHAIBAO9610_ENABLE_REAL_CALLS only permits tracking lookups."
            )

        # NOTE: never log `payload` — it contains the plaintext password and its
        # confirmation.
        #
        # `confirmPassword` and `acceptTerms` are the customer's own values, not
        # values manufactured here. The adapter used to send
        # `confirmPassword = password` and a hard-coded `acceptTerms = True`,
        # which meant a genuine mismatch could never be detected anywhere in the
        # stack and the customer's agreement was asserted on their behalf.
        payload = {
            "name": request.full_name,
            "phone": request.phone,
            "email": request.email,
            "password": request.password,
            "confirmPassword": request.confirm_password,
            "acceptTerms": request.accept_terms,
        }

        try:
            response = self._client.post(
                f"{self.base_url}{self.register_path}",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout(),
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
                error_code="PROVIDER_TIMEOUT",
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
                # A refused connection is an outage, not a slow provider.
                error_code="PROVIDER_UNREACHABLE",
            )

        http_status = response.status_code
        # Body is used only for duplicate detection; it is never logged whole,
        # because an upstream error body can echo request fields.
        body_text = response.text or ""

        if 200 <= http_status < 300:
            identifier, code = self._extract(_safe_json(response))
            if identifier is None and code is None:
                # A 2xx that identifies NOBODY is not a usable success.
                #
                # MEASURED with the owner-authorized one-shot live POST
                # (2026-10-07): the provider answered 2xx and this extraction found
                # nothing at all, so the customer received "Đăng ký thành công" with
                # no customer code. The customer code is the point of registering.
                #
                # It is NOT reported as SUCCESS because a caller cannot tell such a
                # registration apart from a real one, and the customer cannot be
                # told anything useful. It is NOT retried either: the request may
                # well have created an account, so a retry could double-register.
                #
                # `UNUSABLE_RESPONSE` is distinct from UNAVAILABLE on purpose — this
                # is a contract problem, not a network one — and it is not
                # retryable, so nothing upstream will try again automatically.
                logger.warning(
                    "khaibao9610 register returned %s but no customer id or code "
                    "could be extracted; the response shape is not the documented one",
                    http_status,
                )
                return RegistrationResult(
                    status=ProviderStatus.UNUSABLE_RESPONSE,
                    message=(
                        "The provider accepted the registration but returned no "
                        "customer identifier we can read."
                    ),
                    http_status=http_status,
                    retryable=False,
                    error_code="UNUSABLE_RESPONSE",
                    # KEEP THE EVIDENCE, REDACTED AND BOUNDED. Losing this once cost
                    # the whole point of an owner-authorized live test; see the field
                    # comment in base.py.
                    diagnostic_body=_redacted_excerpt(
                        body_text, request.password, request.confirm_password
                    ),
                )
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
                error_code=(
                    "PROVIDER_RATE_LIMITED" if http_status == 429 else "PROVIDER_UNAVAILABLE"
                ),
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
