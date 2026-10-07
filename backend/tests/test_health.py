"""Health endpoint."""

from __future__ import annotations

from app.routers.health import provider_health
from tests.conftest import Harness


def test_health_returns_the_documented_shape(harness: Harness) -> None:
    response = harness.client.get("/api/v1/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "viporder-web"
    assert isinstance(body["version"], str) and body["version"]
    assert body["checks"]["database"] == "ok"
    # The capabilities are reported SEPARATELY so an operator can see whether
    # customer creation is live without reading the env file. In mock mode both are
    # "mock" — and the assertion is exact on purpose: a silently added or removed
    # capability should fail this test.
    assert body["checks"]["provider"] == {
        "mode": "mock",
        "status": "ok",
        "capabilities": {"tracking_reads": "mock", "registration_writes": "mock"},
    }


def test_health_reports_the_configured_mode(make_harness) -> None:
    from app.providers.mock import MockRegistrationProvider

    # The live adapter refuses to build without the double opt-in, which is the
    # point of that control — so mode is asserted with a stub provider.
    harness = make_harness(
        provider=MockRegistrationProvider(),
        khaibao9610_mode="http",
        khaibao9610_enable_real_calls=False,
    )
    body = harness.client.get("/api/v1/health").json()
    assert body["checks"]["provider"] == {
        "mode": "http",
        "status": "disabled",
        "capabilities": {"tracking_reads": "disabled", "registration_writes": "disabled"},
    }


def test_health_reports_db_failure_as_503(make_harness, monkeypatch) -> None:
    harness = make_harness()
    monkeypatch.setattr(harness.database, "is_healthy", lambda: False)

    response = harness.client.get("/api/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["database"] == "error"


def test_provider_health_helper() -> None:
    assert provider_health("mock", False, False) == "ok"
    assert provider_health("http", True, False) == "ok"
    assert provider_health("http", False, True) == "ok"  # writes only: still live
    assert provider_health("http", True, True) == "ok"
    assert provider_health("http", False, False) == "disabled"


def test_capability_status_helper() -> None:
    from app.routers.health import capability_status

    assert capability_status("mock", False) == "mock"
    assert capability_status("http", True) == "live"
    assert capability_status("http", False) == "disabled"


def test_health_shows_writes_disabled_when_only_reads_are_enabled(make_harness) -> None:
    """The staging configuration that caused the incident, asserted at the API.

    Reads on, writes off must be visible in /health — an operator should not have to
    read an env file to learn whether customer creation is live.
    """
    from app.providers.mock import MockRegistrationProvider

    harness = make_harness(
        provider=MockRegistrationProvider(),
        khaibao9610_mode="http",
        khaibao9610_enable_real_calls=True,
        khaibao9610_enable_real_registration=False,
    )
    provider = harness.client.get("/api/v1/health").json()["checks"]["provider"]
    assert provider["capabilities"] == {
        "tracking_reads": "live",
        "registration_writes": "disabled",
    }
    # Reads alone are still "live": the provider IS being reached for real.
    assert provider["status"] == "ok", provider


def test_health_reports_ok_when_only_registration_writes_are_live(make_harness) -> None:
    """The writes-only configuration — the inverse of the incident, and the hole.

    ``KHAIBAO9610_ENABLE_REAL_REGISTRATION=yes`` with
    ``KHAIBAO9610_ENABLE_REAL_CALLS=no`` is the ONE configuration in which this
    application creates real customer accounts on the provider's production API
    while making no tracking calls at all. Before this test, ``status`` was
    derived from the READ switch alone, so this exact configuration reported:

        {"mode":"http","status":"disabled",
         "capabilities":{"tracking_reads":"disabled","registration_writes":"live"}}

    The sibling field said ``live``; the field a monitor reads said ``disabled``.
    Both are asserted here, so the two can never disagree silently again: this
    test fails if ``status`` is derived from either switch alone, and it fails if
    it is hard-coded to ``ok``.

    There was a reads-only test and no writes-only one; the untested half is the
    half that was wrong.
    """
    from app.providers.mock import MockRegistrationProvider

    harness = make_harness(
        provider=MockRegistrationProvider(),
        khaibao9610_mode="http",
        khaibao9610_enable_real_calls=False,
        khaibao9610_enable_real_registration=True,
    )
    provider = harness.client.get("/api/v1/health").json()["checks"]["provider"]
    assert provider == {
        "mode": "http",
        "status": "ok",
        "capabilities": {
            "tracking_reads": "disabled",
            "registration_writes": "live",
        },
    }, provider


def test_health_reports_disabled_only_when_neither_capability_is_live(make_harness) -> None:
    """The other direction: "ok" must not be unconditional."""
    from app.providers.mock import MockRegistrationProvider

    harness = make_harness(
        provider=MockRegistrationProvider(),
        khaibao9610_mode="http",
        khaibao9610_enable_real_calls=False,
        khaibao9610_enable_real_registration=False,
    )
    provider = harness.client.get("/api/v1/health").json()["checks"]["provider"]
    assert provider["status"] == "disabled", provider


def test_health_is_not_rate_limited(make_harness) -> None:
    harness = make_harness(rate_limit_enabled=True, rate_limit_attempts=2)
    for _ in range(5):
        assert harness.client.get("/api/v1/health").status_code == 200
