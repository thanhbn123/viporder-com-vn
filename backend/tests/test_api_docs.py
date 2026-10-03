"""API documentation must not be public in production.

`/docs` and `/openapi.json` enumerate every route, parameter, schema and error
code. That is useful in development and a map for an attacker in production,
where the only intended client is the public form. `/redoc` is never served.
"""

from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize("app_env", ["development", "staging", "test", ""])
def test_docs_are_on_outside_production(make_harness, app_env: str) -> None:
    harness = make_harness(app_env=app_env)
    assert harness.client.get("/api/docs").status_code == 200
    assert harness.client.get("/api/openapi.json").status_code == 200


def test_docs_are_off_in_production(make_harness) -> None:
    harness = make_harness(app_env="production")

    assert harness.client.get("/api/docs").status_code == 404
    assert harness.client.get("/api/openapi.json").status_code == 404
    # The API itself still works.
    assert harness.client.get("/api/v1/health").status_code == 200


def test_production_is_matched_case_insensitively_and_trimmed(make_harness) -> None:
    harness = make_harness(app_env="  PRODUCTION  ")
    assert harness.client.get("/api/docs").status_code == 404


def test_an_operator_can_force_docs_on_in_production(make_harness) -> None:
    harness = make_harness(app_env="production", enable_api_docs=True)
    assert harness.client.get("/api/docs").status_code == 200


def test_an_operator_can_force_docs_off_outside_production(make_harness) -> None:
    harness = make_harness(app_env="development", enable_api_docs=False)
    assert harness.client.get("/api/docs").status_code == 404


def test_redoc_is_never_served(make_harness) -> None:
    harness = make_harness(app_env="development")
    assert harness.client.get("/redoc").status_code == 404


@pytest.mark.parametrize(
    ("app_env", "explicit", "expected"),
    [
        ("production", None, False),
        ("PRODUCTION", None, False),
        ("development", None, True),
        ("staging", None, True),
        ("production", True, True),
        ("development", False, False),
    ],
)
def test_settings_docs_flag(app_env: str, explicit: bool | None, expected: bool) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=app_env,
        enable_api_docs=explicit,
    )
    assert settings.api_docs_enabled is expected
    assert settings.is_production is (app_env.strip().lower() == "production")
