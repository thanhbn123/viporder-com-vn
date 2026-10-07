"""G12 — reads and writes are gated by SEPARATE switches.

WHY THIS FILE EXISTS.

The task permits **real read-only GETs** against the provider and forbids
**uncontrolled registration POSTs**. The code used to make that distinction
**impossible to express**: one switch pair gated both paths, so enabling live
tracking lookups also enabled customer creation.

During staging acceptance that produced an **unauthorised POST** to the provider's
production API and created an account:

    POST https://apiviporder.com/frontend/v1/register "HTTP/1.1 200 OK"
    lead registered lead_id=d9f8841b-… external_code=None

The safe action and the forbidden action were the same action. A warning in a
runbook cannot fix that; a second switch can. These tests are what stops the two
from being merged back into one.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.providers.base import (
    ProviderConfigurationError,
    ProviderUnavailableError,
    RegistrationRequest,
)
from app.providers.factory import build_provider
from app.providers.khaibao9610 import ViporderFrontendProvider


def _provider(*, reads: bool, writes: bool) -> ViporderFrontendProvider:
    """A live provider that never reaches the network.

    ``httpx.MockTransport`` answers everything, so these tests prove which
    CAPABILITY is permitted, not what the upstream returns.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, request=request)

    return ViporderFrontendProvider(
        mode="http",
        enable_real_calls=reads,
        enable_real_registration=writes,
        base_url="https://example.invalid/frontend/v1",
        user_agent="test-agent",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


REQUEST = RegistrationRequest(
    full_name="Khách Kiểm Thử",
    phone="0912345678",
    email="k@example.com",
    password="matkhau123",
    confirm_password="matkhau123",
    accept_terms=True,
)


# ---------------------------------------------------------------------------
# The regression: the exact configuration that caused the incident
# ---------------------------------------------------------------------------


def test_READS_ON_ALONE_does_not_permit_registration() -> None:
    """`ENABLE_REAL_CALLS=yes` and nothing else — the staging configuration.

    This is the one that matters. If it ever passes again, a tracking test can
    create a real customer on somebody else's production system.
    """
    provider = _provider(reads=True, writes=False)
    with pytest.raises(ProviderUnavailableError) as exc:
        provider.register(REQUEST)
    assert "ENABLE_REAL_REGISTRATION" in str(exc.value), (
        "the refusal must name the switch an operator has to set"
    )


def test_reads_on_alone_still_permits_tracking() -> None:
    """...while the reads it was enabled for keep working."""
    provider = _provider(reads=True, writes=False)
    # The MockTransport answers 200; the point is that it does not refuse.
    assert provider.find_warehouse_import("KY1") is not None


def test_WRITES_ON_ALONE_does_not_permit_tracking() -> None:
    provider = _provider(reads=False, writes=True)
    provider.register(REQUEST)  # must not raise
    with pytest.raises(ProviderUnavailableError) as exc:
        provider.find_warehouse_import("KY1")
    assert "ENABLE_REAL_CALLS" in str(exc.value)


def test_both_off_refuses_to_construct() -> None:
    with pytest.raises(ProviderConfigurationError) as exc:
        _provider(reads=False, writes=False)
    message = str(exc.value)
    assert "ENABLE_REAL_CALLS" in message and "ENABLE_REAL_REGISTRATION" in message


# ---------------------------------------------------------------------------
# The same thing through Settings and the factory — where it actually bit
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "khaibao9610_mode": "http",
        "khaibao9610_base_url": "https://example.invalid/frontend/v1",
        "khaibao9610_user_agent": "test-agent",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_the_write_switch_defaults_to_OFF() -> None:
    """A live provider built from settings must not write unless told twice."""
    settings = _settings(khaibao9610_enable_real_calls=True)
    assert settings.khaibao9610_enable_real_registration is False

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, request=request)

    provider = build_provider(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderUnavailableError):
        provider.register(REQUEST)  # type: ignore[attr-defined]


def test_enabling_reads_does_not_flip_the_write_switch() -> None:
    """Stated as its own test because it is the whole point of the split."""
    off = _settings(khaibao9610_enable_real_calls=False)
    on = _settings(khaibao9610_enable_real_calls=True)
    assert off.khaibao9610_enable_real_registration is False
    assert on.khaibao9610_enable_real_registration is False
