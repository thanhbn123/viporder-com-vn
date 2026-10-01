"""FastAPI dependencies.

Routes receive a repository and a service, never a global. Sessions are created
per request and always closed, and the service is built with the app's provider
so tests can inject a failing one without patching module import state.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from .config import Settings
from .repositories.base import LeadRepository
from .services.registration import RegistrationService


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> Iterator[LeadRepository]:
    repository = request.app.state.repository_factory()
    try:
        yield repository
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


def get_service(request: Request) -> Iterator[RegistrationService]:
    repository = request.app.state.repository_factory()
    try:
        yield RegistrationService(
            repository=repository,
            provider=request.app.state.provider,
            settings=request.app.state.settings,
        )
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()
