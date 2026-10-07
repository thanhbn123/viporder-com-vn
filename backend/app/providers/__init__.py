"""Registration provider adapters."""

from .base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderStatus,
    RegistrationProvider,
    RegistrationRequest,
    RegistrationResult,
)
from .factory import build_provider
from .khaibao9610 import ViporderFrontendProvider
from .mock import MockRegistrationProvider

__all__ = [
    "MockRegistrationProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderStatus",
    "RegistrationProvider",
    "RegistrationRequest",
    "RegistrationResult",
    "ViporderFrontendProvider",
    "build_provider",
]
