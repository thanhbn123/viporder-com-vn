"""Persistence abstraction for leads.

The service depends on this protocol, not on SQLAlchemy. That keeps the storage
engine replaceable (and lets tests swap in a fake) without touching route code.

Note what is *absent*: there is no method that takes or returns a password, and
no generic "update anything" escape hatch. The only mutation is
:meth:`LeadRepository.update_status`, which is deliberately narrow.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Lead, RegistrationStatus


@runtime_checkable
class LeadRepository(Protocol):
    """Storage contract for :class:`~app.models.Lead`."""

    def create(self, lead: Lead) -> Lead:
        """Persist a new lead and return it with generated defaults applied."""
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
    ) -> Lead | None:
        """Move a lead to a new registration status, or return ``None``."""
        ...

    def find_registered_by_phone(self, phone: str) -> Lead | None:
        """Return the existing REGISTERED registration lead for a canonical phone."""
        ...

    def list_pending(self, limit: int = 100) -> list[Lead]:
        """Leads awaiting the provider, oldest first — the retry work queue."""
        ...
