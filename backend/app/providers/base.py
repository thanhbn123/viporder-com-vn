"""Provider contract for customer registration.

The upstream (KHAIBAO9610) supplies the customer account. This module defines
the *shape* the rest of the service depends on, so the storage layer never has
to know which provider is behind it and a provider swap is a factory change.

Security note: ``RegistrationRequest`` carries a plaintext password because the
provider needs it to create the account. It is a frozen dataclass with a
``repr=False`` password field so an accidental f-string or traceback cannot
print it, and the adapters must never log the request.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ProviderStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"
    # The provider answered 2xx but identified nobody we can read — no customer id
    # and no customer code. Distinct from UNAVAILABLE on purpose: that is a network
    # or availability problem, this is a CONTRACT problem. Measured with the
    # owner-authorized live POST on 2026-10-07, which is why it exists.
    UNUSABLE_RESPONSE = "UNUSABLE_RESPONSE"


@dataclass(frozen=True)
class RegistrationRequest:
    """What a provider needs to create a customer account.

    ``password`` and ``confirm_password`` are excluded from ``repr`` on purpose.
    They exist in memory for the duration of one provider call and are never
    persisted.

    ``confirm_password`` and ``accept_terms`` are carried through rather than
    manufactured by the adapter. The adapter previously sent
    ``confirmPassword = password`` and a hard-coded ``acceptTerms = True``, which
    made a real "the two passwords differ" check impossible anywhere in the stack
    and asserted the customer's agreement on their behalf. Both now reflect what
    the customer actually submitted; the schema is what guarantees they are sane.
    """

    full_name: str
    phone: str
    password: str = field(repr=False)
    #: What the customer typed in the confirmation field. Equal to ``password``
    #: by the time it gets here — ``RegistrationCreate`` is what enforces that —
    #: but it is passed through so the upstream receives the customer's own
    #: submission rather than a value this server invented.
    confirm_password: str = field(repr=False)
    #: The customer's acceptance of the terms, as submitted. Not defaulted: a
    #: default of ``False`` would silently send a refusal, and a default of
    #: ``True`` would re-create the fabrication this field exists to remove.
    accept_terms: bool
    email: str | None = None
    province: str | None = None
    service_interest: str | None = None

    def __post_init__(self) -> None:
        if not self.password:
            raise ValueError("password is required")
        if self.confirm_password != self.password:
            # Belt and braces. The request schema rejects a mismatch with a 422
            # before this point, so reaching here means a caller built the request
            # by hand. Fail loudly rather than sending the upstream a pair it
            # will reject with a message we cannot explain to the customer.
            raise ValueError("confirm_password must match password")


@dataclass(frozen=True)
class RegistrationResult:
    status: ProviderStatus
    external_customer_id: str | None = None
    external_customer_code: str | None = None
    message: str = ""
    http_status: int | None = None
    retryable: bool = False
    #: Additive extension to the agreed contract: a stable, machine-readable
    #: reason for an UNAVAILABLE result. Without it a connection refusal and a
    #: read timeout are indistinguishable in the stored lead (both arrive with
    #: ``http_status`` None), and a real outage becomes undiagnosable.
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProviderStatus):
            raise TypeError("status must be a ProviderStatus")


@runtime_checkable
class RegistrationProvider(Protocol):
    """Structural contract every provider adapter implements."""

    name: str

    def register(self, request: RegistrationRequest) -> RegistrationResult: ...


class ProviderError(RuntimeError):
    """Base class for provider configuration/runtime failures."""


class ProviderConfigurationError(ProviderError):
    """The adapter was asked to run in a configuration it must refuse.

    Raised rather than silently degrading: reaching a real customer system must
    be a deliberate, two-key decision.
    """


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached, or told us to come back later.

    Covers a read/connect timeout, a transport failure (DNS, TLS, refused
    connection), and an upstream ``429``. All of these are transient: the same
    request may well succeed shortly. The route maps this to ``503
    PROVIDER_UNAVAILABLE``.
    """


class ProviderResponseError(ProviderError):
    """The provider answered, but the answer is unusable.

    Covers an upstream ``5xx`` and a body that is not the JSON object the
    measured contract describes. The distinction from
    :class:`ProviderUnavailableError` matters to an operator: "unreachable" is a
    network or upstream-availability problem, "bad response" means our
    expectation about the contract no longer holds and the code needs looking at.
    The route maps this to ``502 PROVIDER_ERROR``.
    """


def redact_request(request: RegistrationRequest) -> dict[str, object]:
    """A log-safe view of a request: never includes a password.

    ``confirm_password`` is popped as well as ``password``. It is the same value
    in the path this service drives, but a hand-built request may differ, and a
    helper whose whole job is "safe to log" must not depend on another layer
    having already made them equal.
    """
    data = dataclasses.asdict(request)
    data.pop("password", None)
    data.pop("confirm_password", None)
    # Placeholders shown in place of each secret, never a credential.
    data["password"] = "<redacted>"  # nosec B105
    data["confirm_password"] = "<redacted>"  # nosec B105
    return data
