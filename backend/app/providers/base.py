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


@dataclass(frozen=True)
class RegistrationRequest:
    """What a provider needs to create a customer account.

    ``password`` is excluded from ``repr`` on purpose. It exists in memory for
    the duration of one provider call and is never persisted.
    """

    full_name: str
    phone: str
    password: str = field(repr=False)
    email: str | None = None
    province: str | None = None
    service_interest: str | None = None

    def __post_init__(self) -> None:
        if not self.password:
            raise ValueError("password is required")


@dataclass(frozen=True)
class RegistrationResult:
    status: ProviderStatus
    external_customer_id: str | None = None
    external_customer_code: str | None = None
    message: str = ""
    http_status: int | None = None
    retryable: bool = False

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


def redact_request(request: RegistrationRequest) -> dict[str, object]:
    """A log-safe view of a request: never includes the password."""
    data = dataclasses.asdict(request)
    data.pop("password", None)
    # A placeholder shown in place of the password, never a credential.
    data["password"] = "<redacted>"  # nosec B105
    return data
