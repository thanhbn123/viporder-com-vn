"""Persistence abstraction for leads.

The service depends on this protocol, not on SQLAlchemy. That keeps the storage
engine replaceable (and lets tests swap in a fake) without touching route code.

Note what is *absent*: there is no method that takes or returns a password, and
no generic "update anything" escape hatch. The only mutation is
:meth:`LeadRepository.update_status`, which is deliberately narrow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..models import Lead, RegistrationStatus

#: "Leave this field alone." Distinct from ``None``, which means "clear it".
#: Without the distinction, any caller that updates an attempt would silently
#: wipe a stored response it never meant to touch.
UNSET: Any = object()


@runtime_checkable
class LeadRepository(Protocol):
    """Storage contract for :class:`~app.models.Lead`."""

    def create(self, lead: Lead) -> Lead:
        """Persist a new lead and return it with generated defaults applied.

        Raises the implementation's conflict exception when the lead cannot be
        inserted: an in-flight claim for the same phone, or a reused idempotency
        key. Callers must distinguish those two — they mean different things to
        the customer.
        """
        ...

    def release_stale_claims(self, older_than: datetime) -> int:
        """Clear phone claims abandoned by a process that died mid-attempt.

        Returns the number reclaimed. A claim is normally released when an
        attempt reaches a terminal outcome; this is the crash-recovery path, and
        without it a killed process would block that phone permanently.
        """
        ...

    def get(self, lead_id: str) -> Lead | None:
        """Return a lead by primary key, or ``None``."""
        ...

    def get_by_idempotency_key(self, key: str) -> Lead | None:
        """Return the lead previously created for this idempotency key."""
        ...

    def update_status(
        self,
        lead_id: str,
        *,
        status: RegistrationStatus,
        external_customer_id: str | None = None,
        external_customer_code: str | None = None,
        last_error_code: str | None = None,
        last_error_message: str | None = None,
        increment_attempt: bool = False,
        response_status: int | None | Any = UNSET,
        response_body: dict | None | Any = UNSET,
    ) -> Lead | None:
        """Move a lead to a new registration status, or return ``None``.

        ``response_status``/``response_body`` record the exact reply a terminal
        outcome produced, so a replay of the same request can return it
        verbatim. Pass ``None`` to clear them (what a non-terminal ``PENDING``
        write does); omit them to leave them untouched.
        """
        ...

    def find_registered_by_phone(self, phone: str) -> Lead | None:
        """Return the existing REGISTERED registration lead for a canonical phone."""
        ...

    def list_pending(self, limit: int = 100) -> list[Lead]:
        """Leads awaiting the provider, oldest first — the retry work queue.

        NOTE: nothing calls this yet. There is no worker or scheduler in this
        service, so a PENDING lead is completed only by a customer retry or the
        admin route. It exists so that adding that worker is a wiring change
        rather than a design change. See backend/README.md.
        """
        ...
