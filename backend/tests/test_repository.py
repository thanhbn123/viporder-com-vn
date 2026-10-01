"""LeadRepository contract.

The point of this file is that the abstraction is real: the protocol is checked
structurally, and every documented method is exercised against the SQLAlchemy
implementation. If someone swaps the storage engine, these are the behaviours
they must reproduce.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import Lead, LeadType, RegistrationStatus, utcnow
from app.repositories.base import LeadRepository
from app.repositories.sqlalchemy_repo import SqlAlchemyLeadRepository
from tests.conftest import Harness


@pytest.fixture
def repo(harness: Harness):  # type: ignore[no-untyped-def]
    session = harness.database.session()
    try:
        yield SqlAlchemyLeadRepository(session)
    finally:
        session.close()


def _lead(lead_id: str, phone: str, status: RegistrationStatus, **kwargs) -> Lead:  # type: ignore[no-untyped-def]
    now = utcnow()
    defaults: dict[str, object] = {
        "lead_id": lead_id,
        "lead_type": LeadType.REGISTER_LEAD,
        "registration_status": status,
        "full_name": "Khách Thử",
        "phone": phone,
        "phone_display": "0912 345 678",
        "tracking_token": f"token-{lead_id}",
        "attempt_count": 1,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return Lead(**defaults)  # type: ignore[arg-type]


def test_sqlalchemy_repository_satisfies_the_protocol(repo) -> None:  # type: ignore[no-untyped-def]
    assert isinstance(repo, LeadRepository)


def test_create_and_get(repo) -> None:  # type: ignore[no-untyped-def]
    created = repo.create(_lead("l-1", "+84912345678", RegistrationStatus.PENDING))
    assert created.lead_id == "l-1"
    fetched = repo.get("l-1")
    assert fetched is not None and fetched.phone == "+84912345678"


def test_get_returns_none_for_unknown_id(repo) -> None:  # type: ignore[no-untyped-def]
    assert repo.get("missing") is None


def test_get_by_idempotency_key(repo) -> None:  # type: ignore[no-untyped-def]
    repo.create(_lead("l-1", "+84912345678", RegistrationStatus.PENDING, idempotency_key="key-1"))
    found = repo.get_by_idempotency_key("key-1")
    assert found is not None and found.lead_id == "l-1"
    assert repo.get_by_idempotency_key("key-2") is None
    assert repo.get_by_idempotency_key("") is None


def test_update_status_to_registered(repo) -> None:  # type: ignore[no-untyped-def]
    repo.create(
        _lead(
            "l-1",
            "+84912345678",
            RegistrationStatus.PENDING,
            last_error_code="PROVIDER_UNAVAILABLE",
            last_error_message="down",
        )
    )

    updated = repo.update_status(
        "l-1",
        status=RegistrationStatus.REGISTERED,
        external_customer_id="ext-1",
        external_customer_code="TT00001",
    )
    assert updated is not None
    assert updated.registration_status is RegistrationStatus.REGISTERED
    assert updated.external_customer_code == "TT00001"
    # Success clears the stale error, so the row describes the latest attempt.
    assert updated.last_error_code is None
    assert updated.last_error_message is None


def test_update_status_increments_attempt_count(repo) -> None:  # type: ignore[no-untyped-def]
    repo.create(_lead("l-1", "+84912345678", RegistrationStatus.PENDING))
    updated = repo.update_status("l-1", status=RegistrationStatus.PENDING, increment_attempt=True)
    assert updated is not None and updated.attempt_count == 2


def test_update_status_returns_none_for_unknown_lead(repo) -> None:  # type: ignore[no-untyped-def]
    assert repo.update_status("missing", status=RegistrationStatus.FAILED) is None


def test_update_status_truncates_a_long_error_message(repo) -> None:  # type: ignore[no-untyped-def]
    repo.create(_lead("l-1", "+84912345678", RegistrationStatus.PENDING))
    updated = repo.update_status(
        "l-1",
        status=RegistrationStatus.FAILED,
        last_error_code="X",
        last_error_message="e" * 900,
    )
    assert updated is not None
    assert len(updated.last_error_message) == 500


def test_find_registered_by_phone(repo) -> None:  # type: ignore[no-untyped-def]
    repo.create(_lead("pending", "+84912345678", RegistrationStatus.PENDING))
    assert repo.find_registered_by_phone("+84912345678") is None

    repo.create(_lead("done", "+84912345678", RegistrationStatus.REGISTERED))
    found = repo.find_registered_by_phone("+84912345678")
    assert found is not None and found.lead_id == "done"


def test_find_registered_by_phone_ignores_quote_leads(repo) -> None:  # type: ignore[no-untyped-def]
    repo.create(
        _lead(
            "quote",
            "+84912345678",
            RegistrationStatus.REGISTERED,
            lead_type=LeadType.QUOTE_LEAD,
        )
    )
    assert repo.find_registered_by_phone("+84912345678") is None


def test_list_pending_returns_oldest_first(repo) -> None:  # type: ignore[no-untyped-def]
    base = utcnow()
    repo.create(
        _lead(
            "newer",
            "+84912000012",
            RegistrationStatus.PENDING,
            created_at=base,
            updated_at=base,
        )
    )
    repo.create(
        _lead(
            "older",
            "+84912000011",
            RegistrationStatus.PENDING,
            created_at=base - timedelta(minutes=5),
            updated_at=base - timedelta(minutes=5),
        )
    )
    repo.create(_lead("done", "+84912000013", RegistrationStatus.REGISTERED))

    assert [lead.lead_id for lead in repo.list_pending()] == ["older", "newer"]


def test_list_pending_respects_the_limit(repo) -> None:  # type: ignore[no-untyped-def]
    for index in range(5):
        repo.create(_lead(f"l-{index}", f"+8491200001{index}", RegistrationStatus.PENDING))
    assert len(repo.list_pending(limit=2)) == 2


def test_indexes_exist_on_the_table() -> None:
    indexes = {index.name for index in Lead.__table__.indexes}
    assert {
        "ix_leads_phone",
        "ix_leads_registration_status",
        "ix_leads_created_at",
        "uq_leads_idempotency_key",
        "uq_leads_tracking_token",
        "uq_leads_registered_phone",
    } <= indexes
