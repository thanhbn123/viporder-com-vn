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
    assert body["checks"]["provider"] == {"mode": "mock", "status": "ok"}


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
    assert body["checks"]["provider"] == {"mode": "http", "status": "disabled"}


def test_health_reports_db_failure_as_503(make_harness, monkeypatch) -> None:
    harness = make_harness()
    monkeypatch.setattr(harness.database, "is_healthy", lambda: False)

    response = harness.client.get("/api/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["database"] == "error"


def test_provider_health_helper() -> None:
    assert provider_health("mock", False) == "ok"
    assert provider_health("http", True) == "ok"
    assert provider_health("http", False) == "disabled"


def test_health_is_not_rate_limited(make_harness) -> None:
    harness = make_harness(rate_limit_enabled=True, rate_limit_attempts=2)
    for _ in range(5):
        assert harness.client.get("/api/v1/health").status_code == 200
