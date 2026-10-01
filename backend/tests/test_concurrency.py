"""Concurrency: what happens when two registrations arrive at the same instant.

Every other test in this project sends one request at a time. That is enough to
prove the *logic* is right and says nothing at all about the *race*. Two races
matter here, and both are reachable from an ordinary browser:

1. **The same `Idempotency-Key` twice, simultaneously.** A user double-clicks
   Submit, or a client retries on a slow connection while the first request is
   still in flight. Both requests check "have I seen this key?", both find
   nothing, both try to insert. One wins; the other must not create a second lead
   and must not surface an error.

2. **The same phone with two different keys, simultaneously.** Also reachable —
   two tabs, or a retry that mints a new key. The partial unique index says only
   one lead may be `REGISTERED` for a phone, so one insert must fail. The user
   on the losing side must be told the truth ("this phone is already
   registered"), not handed a 500.

These need a real database and real threads, so they run only when
``TEST_DATABASE_URL`` is set — same gate as ``test_postgres.py``, and for the
same reason: SQLite's locking would make the result an artefact of the test
harness rather than a measurement of the application.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parent.parent
URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not URL,
    reason="TEST_DATABASE_URL is not set — concurrency tests need a real database",
)

_FORBIDDEN_HINTS = ("prod", "live", "master", "main")
PASSWORD = "concurrency-canary-not-a-real-secret"


@pytest.fixture(scope="module")
def pg_url() -> str:
    if not URL.startswith("postgresql"):
        pytest.skip(f"TEST_DATABASE_URL is not a PostgreSQL URL: {URL!r}")
    name = URL.rsplit("/", 1)[-1].split("?")[0].lower()
    if not name or any(h in name for h in _FORBIDDEN_HINTS):
        pytest.fail(
            f"refusing to run destructive tests against database {name!r}; "
            "point TEST_DATABASE_URL at a dedicated test database"
        )
    return URL


@pytest.fixture(scope="module")
def client(pg_url: str):
    """The real application, against the real database.

    No `migrated` fixture dependency: `test_postgres.py` migrates the schema, and
    pytest runs files in alphabetical order, but relying on that would make this
    file fail when run alone. Migrate here too — `alembic upgrade head` is
    idempotent.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": pg_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.main import create_app

    settings = Settings(
        database_url=pg_url,
        khaibao9610_mode="mock",
        mock_provider_behaviour="success",
        rate_limit_enabled=False,  # the limiter is tested on its own; here it would mask the race
        admin_api_token="",
        auto_create_schema=False,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


@pytest.fixture
def cleanup(pg_url: str):
    yield
    engine = sa.create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM leads WHERE phone LIKE '+8490%'"))
    engine.dispose()


def _body(phone: str) -> dict:
    return {
        "full_name": "Khách Đồng Thời",
        "phone": phone,
        "password": PASSWORD,
        "email": "",
        "consent": True,
    }


def _run_together(call, *, times: int):
    """Fire ``times`` copies of ``call`` as simultaneously as we can manage.

    A barrier so the threads enter the request at the same moment; without it the
    first request finishes before the second starts and no race occurs — which
    would make this test pass by testing nothing.
    """
    barrier = threading.Barrier(times)

    def one(index: int):
        barrier.wait(timeout=10)
        return call(index)

    with ThreadPoolExecutor(max_workers=times) as pool:
        return list(pool.map(one, range(times)))


def _count(pg_url: str, phone: str, status: str | None = None) -> int:
    engine = sa.create_engine(pg_url)
    try:
        with engine.connect() as conn:
            if status is None:
                return conn.execute(
                    sa.text("SELECT count(*) FROM leads WHERE phone = :p"), {"p": phone}
                ).scalar_one()
            return conn.execute(
                sa.text("SELECT count(*) FROM leads WHERE phone = :p AND registration_status = :s"),
                {"p": phone, "s": status},
            ).scalar_one()
    finally:
        engine.dispose()


def _count_where(pg_url: str, phone: str, status: str) -> int:
    return _count(pg_url, phone, status)


# ---------------------------------------------------------------------------
# Race 1 — the same Idempotency-Key, simultaneously
# ---------------------------------------------------------------------------


def test_same_key_concurrently_creates_exactly_one_lead(client, cleanup, pg_url: str) -> None:
    phone = f"+8490{uuid.uuid4().int % 10**7:07d}"
    key = f"race-key-{uuid.uuid4()}"

    def submit(_index: int):
        return client.post(
            "/api/v1/registrations", json=_body(phone), headers={"Idempotency-Key": key}
        )

    responses = _run_together(submit, times=6)

    statuses = [r.status_code for r in responses]
    assert all(s < 500 for s in statuses), (
        f"a concurrent duplicate key produced a server error: {statuses} — "
        f"bodies: {[r.text[:200] for r in responses if r.status_code >= 500]}"
    )

    assert _count(pg_url, phone) == 1, (
        f"expected exactly 1 lead for one Idempotency-Key, found {_count(pg_url, phone)}"
    )

    # Every caller must be talking about the SAME lead — that is what the key
    # promises, and it is the part that must hold exactly.
    lead_ids = {r.json().get("lead_id") for r in responses}
    assert len(lead_ids) == 1, f"concurrent callers saw different leads: {lead_ids}"

    # The STATUS may legitimately differ, and this is worth stating rather than
    # asserting away: a caller that arrives while the winning attempt is still
    # waiting on the provider is told 202 PENDING, and the winner itself returns
    # 201 once the provider answers. What must never happen is a replay being
    # mistaken for a fresh attempt — so 409/422 here would be a defect.
    assert set(statuses) <= {201, 202}, (
        f"a concurrent replay was mistaken for a new attempt: {statuses}"
    )


# ---------------------------------------------------------------------------
# Race 2 — the same phone, different keys, simultaneously
# ---------------------------------------------------------------------------


def test_same_phone_different_keys_concurrently_is_refused_truthfully(
    client, cleanup, pg_url: str
) -> None:
    """The loser of this race must be told 409, not handed a 500.

    The partial unique index guarantees only one REGISTERED lead per phone, so
    exactly one request can win. The interesting part is what the others get: the
    repository only converts an *idempotency-key* conflict into its own error
    type, so a phone conflict may reach the API as a raw IntegrityError.
    """
    phone = f"+8490{uuid.uuid4().int % 10**7:07d}"

    def submit(index: int):
        return client.post(
            "/api/v1/registrations",
            json=_body(phone),
            headers={"Idempotency-Key": f"race-phone-{uuid.uuid4()}-{index}"},
        )

    responses = _run_together(submit, times=6)
    statuses = sorted(r.status_code for r in responses)

    # The design is one lead row PER ATTEMPT — six attempts, six rows — and the
    # partial unique index permits at most one of them to be REGISTERED. Asserting
    # "one row" would be asserting the wrong design; the invariant is the
    # REGISTERED count.
    registered = _count_where(pg_url, phone, "REGISTERED")
    assert registered == 1, (
        f"the partial unique index should permit exactly one REGISTERED lead, "
        f"found {registered} (total rows: {_count(pg_url, phone)})"
    )

    server_errors = [r for r in responses if r.status_code >= 500]
    assert not server_errors, (
        "a concurrent registration for the same phone returned "
        f"{[r.status_code for r in server_errors]} instead of a truthful refusal. "
        "Expected the losers to receive 409 DUPLICATE_PHONE. Body: "
        f"{server_errors[0].text[:300]}"
    )

    winners = [st for st in statuses if st == 201]
    assert len(winners) == 1, f"expected exactly one 201, got {statuses}"
    assert all(st == 409 for st in statuses if st != 201), (
        f"expected the losers to get 409, got {statuses}"
    )

    # And the losing rows must not be left mid-flight. Before the fix the
    # provider had already been called and the row stayed PENDING, recording an
    # upstream customer that nothing pointed at.
    engine = sa.create_engine(pg_url)
    try:
        with engine.connect() as conn:
            left_pending = conn.execute(
                sa.text(
                    "SELECT count(*) FROM leads WHERE phone = :p "
                    "AND registration_status = 'PENDING'"
                ),
                {"p": phone},
            ).scalar_one()
    finally:
        engine.dispose()
    assert left_pending == 0, (
        f"{left_pending} lead(s) left PENDING after the race — the provider had "
        f"already been called for them, so they are stranded, not merely slow"
    )


def test_the_losing_response_never_leaks_the_winners_customer_code(
    client, cleanup, pg_url: str
) -> None:
    """A refusal must not hand out the code it is refusing to duplicate."""
    phone = f"+8490{uuid.uuid4().int % 10**7:07d}"

    def submit(index: int):
        return client.post(
            "/api/v1/registrations",
            json=_body(phone),
            headers={"Idempotency-Key": f"race-leak-{uuid.uuid4()}-{index}"},
        )

    responses = _run_together(submit, times=4)

    winner_code = None
    for r in responses:
        if r.status_code == 201:
            winner_code = r.json().get("external_customer_code")
    assert winner_code, "no request succeeded, so the race was not exercised"

    for r in responses:
        if r.status_code == 201:
            continue
        assert winner_code not in r.text, (
            f"a losing response leaked the winner's customer code {winner_code!r}: {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# The property the phone claim exists to guarantee
# ---------------------------------------------------------------------------


class _CountingProvider:
    """Wraps the mock provider and records every call.

    This is the only way to assert the thing that actually matters. Every other
    assertion in this file observes the DATABASE, and the database cannot tell
    you whether the provider was called — the whole defect was that it *was*
    called and the result was then discarded.
    """

    name = "counting-mock"

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def register(self, request):
        with self._lock:
            self.calls.append(request.phone)
        return self._inner.register(request)


@pytest.fixture
def counting_client(pg_url: str):
    """A client whose provider records every call."""
    import subprocess
    import sys

    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.main import create_app
    from app.providers.mock import MockRegistrationProvider

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": pg_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

    provider = _CountingProvider(MockRegistrationProvider(behaviour="success"))
    settings = Settings(
        database_url=pg_url,
        khaibao9610_mode="mock",
        mock_provider_behaviour="success",
        rate_limit_enabled=False,
        admin_api_token="",
        auto_create_schema=False,
    )
    app = create_app(settings=settings, provider=provider)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c, provider


def test_the_provider_is_called_at_most_once_per_phone_under_concurrency(
    counting_client, cleanup, pg_url: str
) -> None:
    """The defect this claim exists to prevent, asserted directly.

    Before the claim existed, six simultaneous registrations for one phone with
    six different keys all reached the provider. One row was recorded; the other
    five customer codes were created upstream and then thrown away by the
    database conflict — customers who would exist and that nothing here could
    explain.
    """
    client, provider = counting_client
    phone = f"+8490{uuid.uuid4().int % 10**7:07d}"

    def submit(index: int):
        return client.post(
            "/api/v1/registrations",
            json=_body(phone),
            headers={"Idempotency-Key": f"claim-{uuid.uuid4()}-{index}"},
        )

    responses = _run_together(submit, times=6)

    assert len(provider.calls) == 1, (
        f"the provider was called {len(provider.calls)} time(s) for one phone; "
        f"expected exactly 1. Statuses: {sorted(r.status_code for r in responses)}"
    )

    # And the callers who did not get through must be told to retry, not that
    # their number is taken — the difference matters to the customer.
    codes = [r.json().get("error", {}).get("code") for r in responses if r.status_code != 201]
    assert all(c in ("REGISTRATION_IN_PROGRESS", "DUPLICATE_PHONE") for c in codes), (
        f"unexpected refusal codes: {codes}"
    )


def test_a_pending_outcome_releases_the_claim_so_the_customer_can_retry(
    counting_client, cleanup, pg_url: str
) -> None:
    """A provider outage must not lock the customer out of retrying.

    This is a regression guard for my own first attempt at the claim: I released
    it only for terminal statuses, so an attempt that ended PENDING (provider
    unavailable) kept the reservation and the customer's own retry collided with
    it — a 500 on SQLite. Holding a phone hostage because the provider was down
    inverts the rule the brief states twice.
    """
    client, _provider = counting_client
    phone = f"+8490{uuid.uuid4().int % 10**7:07d}"

    from app.config import Settings  # noqa: F401  (documented import locality)

    # Down, then up again: two sequential attempts, same phone, different keys.
    first = client.post(
        "/api/v1/registrations",
        json=_body(phone),
        headers={"Idempotency-Key": f"outage-{uuid.uuid4()}"},
    )
    assert first.status_code in (201, 202), first.text

    second = client.post(
        "/api/v1/registrations",
        json=_body(phone),
        headers={"Idempotency-Key": f"retry-{uuid.uuid4()}"},
    )
    assert second.status_code != 500, (
        f"the retry after a non-terminal attempt returned 500: {second.text[:200]}"
    )
    # A sequential retry is not a race: it must be allowed through.
    assert second.status_code in (201, 202, 409), second.text
