"""LeadRepository contract.

The point of this file is that the abstraction is real: the protocol is checked
structurally, and every documented method is exercised against the SQLAlchemy
implementation. If someone swaps the storage engine, these are the behaviours
they must reproduce.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

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
    """Every index the model declares must actually be declared.

    This used to list six names and compare with `<=`, so it was neither
    exhaustive nor self-maintaining — it silently stopped describing the table
    the moment an index was added or replaced. It now asserts the *properties*
    that matter: the phone rule is a single partial unique index, and the
    lookup indexes exist.
    """
    by_name = {index.name: index for index in Lead.__table__.indexes}

    # The phone rule is ONE index, because one rule is what it states. Two
    # overlapping partial indexes left the window CI found (0005's docstring).
    phone_rules = [i for i in by_name.values() if i.unique and "phone" in i.columns]
    assert [i.name for i in phone_rules] == ["uq_leads_live_phone"], (
        f"expected exactly one unique phone rule, found {[i.name for i in phone_rules]}"
    )
    live = phone_rules[0]
    predicate = str(live.dialect_options["postgresql"]["where"])
    assert "in_flight_at IS NOT NULL" in predicate
    assert "REGISTERED" in predicate, (
        "the rule must also cover already-registered rows, or an attempt that "
        "starts after another completes can call the provider a second time"
    )

    for name in (
        "ix_leads_phone",
        "ix_leads_registration_status",
        "ix_leads_created_at",
        "uq_leads_idempotency_key",
        "uq_leads_tracking_token",
    ):
        assert name in by_name, f"{name} is missing from the table"


# --- the retry work queue has no consumer -----------------------------------


def test_list_pending_has_no_production_caller() -> None:
    """`list_pending` is a queue with nothing reading it.

    There is no scheduler, worker, cron job or background task in this service,
    so a PENDING lead is completed only by a customer retry or the admin route.
    That is a documented limitation, not a hidden bug — and this test is what
    keeps the documentation honest.

    If this test fails, someone has wired a worker. That is good news: move the
    note in backend/README.md out of "Known limitations" and describe the worker
    (its cadence, its retry policy, and what happens on repeated failure).
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    callers = {
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if "list_pending" in path.read_text(encoding="utf-8")
    }

    # routers/admin.py READS the queue for a human (the follow-up list); it is
    # not a worker and retries nothing. Anything beyond that is a worker.
    assert callers == {
        "repositories/base.py",
        "repositories/sqlalchemy_repo.py",
        "routers/admin.py",
    }, (
        "list_pending gained a caller in "
        f"{sorted(callers - {'repositories/base.py', 'repositories/sqlalchemy_repo.py', 'routers/admin.py'})}. "
        "Update the retry note in backend/README.md."
    )


def test_no_scheduler_or_worker_exists() -> None:
    """The other half of the same claim, asserted rather than assumed."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    source = "\n".join(path.read_text(encoding="utf-8") for path in app_dir.rglob("*.py"))

    for forbidden in (
        "BackgroundTasks",
        "apscheduler",
        "celery",
        "from rq",
        "import rq",
        "schedule.every",
    ):
        assert forbidden not in source, f"a worker appeared: {forbidden}"


# ---------------------------------------------------------------------------
# Stale phone claims — the crash-recovery path
# ---------------------------------------------------------------------------
#
# This path had NO test. It is the one that decides whether a process killed
# mid-attempt locks a customer's phone number out permanently, which is a worse
# failure than the duplicate provider call the claim exists to prevent. The
# concurrency tests cover the live race; nothing covered the abandoned-claim case.


def test_release_stale_claims_frees_an_abandoned_claim(repo) -> None:  # type: ignore[no-untyped-def]
    abandoned = _lead(
        "stale-1",
        "+84912340001",
        RegistrationStatus.PENDING,
        in_flight_at=utcnow() - timedelta(hours=1),
    )
    repo.create(abandoned)

    reclaimed = repo.release_stale_claims(utcnow() - timedelta(seconds=120))

    assert reclaimed == 1, f"expected one abandoned claim to be reclaimed, got {reclaimed}"
    refreshed = repo.get("stale-1")
    assert refreshed is not None
    assert refreshed.in_flight_at is None, "the claim was not actually cleared"


def test_release_stale_claims_leaves_a_LIVE_claim_alone(repo) -> None:  # type: ignore[no-untyped-def]
    """The negative that matters: reclamation must not free a running attempt.

    A reclaim that is too eager reintroduces the duplicate provider call —
    two attempts in flight for the same phone — which is the whole defect.
    """
    live = _lead("live-1", "+84912340002", RegistrationStatus.PENDING, in_flight_at=utcnow())
    repo.create(live)

    reclaimed = repo.release_stale_claims(utcnow() - timedelta(seconds=120))

    assert reclaimed == 0, f"a live claim was reclaimed after only {0}s, not 120s"
    refreshed = repo.get("live-1")
    assert refreshed is not None and refreshed.in_flight_at is not None


def test_a_lead_with_no_claim_is_never_touched(repo) -> None:  # type: ignore[no-untyped-def]
    """Rows that are PENDING but not in flight are ordinary retryable leads."""
    idle = _lead("idle-1", "+84912340003", RegistrationStatus.PENDING)
    repo.create(idle)
    assert repo.release_stale_claims(utcnow() + timedelta(days=365)) == 0


def test_reclaiming_frees_the_phone_for_a_new_attempt(repo) -> None:  # type: ignore[no-untyped-def]
    """The point of reclaiming: the next attempt can take the claim.

    Asserted end to end rather than by inspecting the column, because "the
    timestamp is null" is not the property anyone cares about — "a customer can
    register again" is.
    """
    blocked = _lead(
        "stale-2",
        "+84912340004",
        RegistrationStatus.PENDING,
        in_flight_at=utcnow() - timedelta(hours=1),
    )
    repo.create(blocked)

    # While the abandoned claim stands, a second attempt for the same phone is
    # refused at INSERT by the partial unique index.
    from app.repositories.sqlalchemy_repo import PhoneBusyError

    with pytest.raises(PhoneBusyError):
        repo.create(
            _lead("blocked-2", "+84912340004", RegistrationStatus.PENDING, in_flight_at=utcnow())
        )

    repo.release_stale_claims(utcnow() - timedelta(seconds=120))
    successor = repo.create(
        _lead("successor-2", "+84912340004", RegistrationStatus.PENDING, in_flight_at=utcnow())
    )
    assert successor.lead_id == "successor-2"


def test_a_registered_phone_cannot_be_claimed_by_a_new_attempt(repo) -> None:  # type: ignore[no-untyped-def]
    """The window CI found, made DETERMINISTIC — no threads, no timing.

    CI caught a case a faster local machine did not, roughly once in twelve:
    request A completes and releases its claim, then request B — whose duplicate
    pre-check ran before A committed — inserts into the now-free claim, calls the
    provider a second time, and only then collides with A's REGISTERED row.

    That was possible while the claim index covered only `in_flight_at`. It now
    covers registered rows too, so B's insert is refused by the database alone.
    This test reproduces the essential condition with no concurrency at all: a
    REGISTERED row exists, and a new attempt tries to take the phone.
    """
    from app.repositories.sqlalchemy_repo import PhoneBusyError

    phone = "+84912340006"
    repo.create(_lead("done-1", phone, RegistrationStatus.REGISTERED, in_flight_at=None))

    # A fresh attempt, claim taken, no pre-check consulted: exactly B's position.
    with pytest.raises(PhoneBusyError):
        repo.create(_lead("late-1", phone, RegistrationStatus.PENDING, in_flight_at=utcnow()))

    # And nothing was half-written.
    assert repo.get("late-1") is None


def test_claiming_an_existing_lead_refuses_when_the_phone_is_busy(repo) -> None:  # type: ignore[no-untyped-def]
    """The operator retry path takes the same claim as the customer path.

    It re-attempts an EXISTING row and so never went through `create`, which is
    where the claim is normally taken. That left the race open on the one path a
    human drives by hand.
    """
    from app.repositories.sqlalchemy_repo import PhoneBusyError

    holder = _lead("holder-1", "+84912340005", RegistrationStatus.PENDING, in_flight_at=utcnow())
    repo.create(holder)
    other = _lead("other-1", "+84912340005", RegistrationStatus.FAILED, in_flight_at=None)
    repo.create(other)

    with pytest.raises(PhoneBusyError):
        repo.claim_phone("other-1")
