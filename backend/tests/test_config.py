"""Configuration defaults and the `.env.example` contract.

``.env.example`` documents every key with an EMPTY value. Copying it verbatim
must therefore be harmless — this file proves that, and pins the defaults the
rest of the system (and the docs) depend on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import LOGIN_URL, Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

KNOWN_KEYS = {
    "APP_ENV",
    "SERVICE_NAME",
    "APP_VERSION",
    "DATABASE_URL",
    "AUTO_CREATE_SCHEMA",
    "KHAIBAO9610_MODE",
    "KHAIBAO9610_BASE_URL",
    "KHAIBAO9610_TIMEOUT_SECONDS",
    "KHAIBAO9610_ENABLE_REAL_CALLS",
    "KHAIBAO9610_USER_AGENT",
    "MOCK_PROVIDER_BEHAVIOUR",
    "ADMIN_API_TOKEN",
    "MAX_REQUEST_BYTES",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_ATTEMPTS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "TRUST_PROXY_HEADERS",
    "CORS_ALLOW_ORIGINS",
}


def test_env_example_exists_and_covers_every_key() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=.*$", text, flags=re.MULTILINE))
    assert documented == KNOWN_KEYS


def test_env_example_values_are_all_empty() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for key, value in re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, flags=re.MULTILINE):
        assert value == "", f"{key} must ship with an empty value, got {value!r}"


def test_env_example_never_contains_a_credential_looking_value() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8").upper()
    for marker in ("BEGIN PRIVATE KEY", "GHO_", "SK-", "AKIA"):
        assert marker not in text


def test_empty_environment_variables_fall_back_to_defaults(monkeypatch) -> None:
    """Every documented key set to "" must behave exactly like "unset"."""
    for key in KNOWN_KEYS:
        monkeypatch.setenv(key, "")
    # Make sure no stray .env in the repository leaks in.
    monkeypatch.chdir(REPO_ROOT)
    settings = get_settings()

    assert settings.khaibao9610_mode == "mock"
    assert settings.khaibao9610_enable_real_calls is False
    assert settings.database_url == "sqlite:///./var/viporder.db"
    assert settings.admin_api_token == ""
    assert settings.admin_enabled is False
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_attempts == 10
    assert settings.rate_limit_window_seconds == 600
    assert settings.max_request_bytes == 64 * 1024
    assert settings.trust_proxy_headers is False
    assert settings.cors_origins == []


def test_defaults(monkeypatch) -> None:
    for key in KNOWN_KEYS:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.service_name == "viporder-web"
    assert settings.khaibao9610_mode == "mock"
    assert settings.khaibao9610_timeout_seconds == 10.0
    assert settings.login_url == LOGIN_URL
    assert LOGIN_URL == "https://khachhang.viporder.com.vn"


@pytest.mark.parametrize("value", ["0.5", "31", "120"])
def test_timeout_outside_the_allowed_range_is_rejected(monkeypatch, value: str) -> None:
    from pydantic import ValidationError

    monkeypatch.setenv("KHAIBAO9610_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_yes_no_spellings_are_accepted(monkeypatch) -> None:
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "yes")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "no")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trust_proxy_headers is True
    assert settings.rate_limit_enabled is False


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(cors_allow_origins=" https://a.example ,, https://b.example ")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_unknown_mode_is_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(khaibao9610_mode="live")


def test_login_url_is_not_configurable(monkeypatch) -> None:
    """No environment key may move the customer portal destination."""
    monkeypatch.setenv("LOGIN_URL", "https://evil.example")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.login_url == "https://khachhang.viporder.com.vn"


def test_dotenv_file_is_not_committed() -> None:
    """A real .env must never be tracked — the repository is public."""
    assert not (REPO_ROOT / ".env").exists()
