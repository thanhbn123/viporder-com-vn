"""Provider selection.

One place decides which adapter the service talks to. Keeping it in a factory
means the routes depend on the :class:`~app.providers.base.RegistrationProvider`
protocol only, and swapping providers is a config change rather than a code
change.
"""

from __future__ import annotations

import httpx

from ..config import Settings
from .base import RegistrationProvider
from .khaibao9610 import ViporderFrontendProvider
from .mock import MockRegistrationProvider


def build_provider(
    settings: Settings, *, client: httpx.Client | None = None
) -> RegistrationProvider:
    """Build the configured provider.

    ``mock`` (the default) never touches the network. ``http`` constructs the
    live adapter, which itself refuses to run unless real calls are explicitly
    enabled.
    """
    mode = settings.khaibao9610_mode

    if mode == "mock":
        return MockRegistrationProvider(settings.mock_provider_behaviour)

    if mode == "http":
        return ViporderFrontendProvider.from_settings(settings, client=client)

    raise ValueError(f"Unsupported KHAIBAO9610_MODE: {mode!r}")
