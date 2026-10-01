"""Deterministic mock registration provider.

This is the DEFAULT provider. The real upstream contract has not been supplied,
so nothing in the default path may guess at it.

Two ways to select behaviour:

1. ``MOCK_PROVIDER_BEHAVIOUR`` environment variable — applies to every request
   handled by this provider instance. One of ``success`` (default),
   ``duplicate``, ``invalid``, ``unavailable``, ``timeout``, ``error``.

2. Phone-suffix convention — lets a single running process answer differently
   per request, which is what tests and manual QA need. The canonical phone
   must end with one of these four digits, where ``X`` is any digit:

   ==============  ==================
   phone ends with behaviour
   ==============  ==================
   ``0000``        duplicate
   ``0001``        invalid
   ``0002``        unavailable
   ``0003``        timeout
   ``0004``        error (raises)
   ``0005`` ...    success
   ==============  ==================

   The suffix wins over the environment variable, so a test can pin the
   instance to ``success`` and still exercise a duplicate for one request.
"""

from __future__ import annotations

import itertools
import threading
import time

from .base import (
    ProviderStatus,
    RegistrationRequest,
    RegistrationResult,
)

SUFFIX_BEHAVIOURS: dict[str, str] = {
    "0000": "duplicate",
    "0001": "invalid",
    "0002": "unavailable",
    "0003": "timeout",
    "0004": "error",
}

_VALID_BEHAVIOURS = {"success", "duplicate", "invalid", "unavailable", "timeout", "error"}


class MockRegistrationProvider:
    """In-process provider. Never touches the network."""

    name = "mock"

    def __init__(self, behaviour: str = "success", *, timeout_seconds: float = 0.0) -> None:
        if behaviour not in _VALID_BEHAVIOURS:
            raise ValueError(
                f"MOCK_PROVIDER_BEHAVIOUR must be one of {sorted(_VALID_BEHAVIOURS)}, "
                f"got {behaviour!r}"
            )
        self.behaviour = behaviour
        self.timeout_seconds = timeout_seconds
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    # -- behaviour selection -------------------------------------------------

    def behaviour_for(self, phone: str) -> str:
        suffix = phone[-4:] if len(phone) >= 4 else ""
        return SUFFIX_BEHAVIOURS.get(suffix, self.behaviour)

    def _next_sequence(self) -> int:
        with self._lock:
            return next(self._counter)

    # -- provider contract ---------------------------------------------------

    def register(self, request: RegistrationRequest) -> RegistrationResult:
        behaviour = self.behaviour_for(request.phone)

        if behaviour == "timeout":
            if self.timeout_seconds:
                time.sleep(self.timeout_seconds)
            return RegistrationResult(
                status=ProviderStatus.UNAVAILABLE,
                message="Provider timed out.",
                http_status=None,
                retryable=True,
            )

        if behaviour == "unavailable":
            return RegistrationResult(
                status=ProviderStatus.UNAVAILABLE,
                message="Provider is unavailable.",
                http_status=503,
                retryable=True,
            )

        if behaviour == "error":
            # A provider that blows up must not be able to destroy the lead;
            # the caller treats any exception as UNAVAILABLE.
            raise RuntimeError("mock provider failure (intentional)")

        if behaviour == "duplicate":
            return RegistrationResult(
                status=ProviderStatus.DUPLICATE,
                message="This phone number is already registered.",
                http_status=409,
                retryable=False,
            )

        if behaviour == "invalid":
            return RegistrationResult(
                status=ProviderStatus.INVALID,
                message="The provider rejected these details.",
                http_status=422,
                retryable=False,
            )

        sequence = self._next_sequence()
        return RegistrationResult(
            status=ProviderStatus.SUCCESS,
            external_customer_id=f"mock-customer-{sequence:05d}",
            external_customer_code=f"TT{sequence:05d}",
            message="Registration completed.",
            http_status=201,
            retryable=False,
        )
