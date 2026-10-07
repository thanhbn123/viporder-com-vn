"""G12 — every provider setting must be reachable through the deployment.

WHY THIS EXISTS. PR #56 added `KHAIBAO9610_ENABLE_REAL_REGISTRATION` to
`config.py` and to `deploy/env.production.example`, but **not** to
`deploy/docker-compose.yml`. Under compose the container therefore never saw it,
`registration_writes` stayed `disabled`, and the capability was **unreachable** —
which surfaced only when someone tried to use it, inside a window where the owner
had authorized a single POST.

A setting that no deployment path passes is invisible. This test makes it loud.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = ROOT / "deploy" / "docker-compose.yml"


def _provider_settings() -> set[str]:
    from app.config import Settings

    return {name.upper() for name in Settings.model_fields if name.startswith("khaibao9610_")}


def test_compose_passes_every_provider_setting() -> None:
    """Any provider setting compose does not pass is unreachable there."""
    text = COMPOSE.read_text(encoding="utf-8")
    passed = set(re.findall(r"(KHAIBAO9610_[A-Z0-9_]+)\s*:", text))

    missing = sorted(_provider_settings() - passed)
    assert not missing, (
        f"deploy/docker-compose.yml does not pass {missing}. Under compose those "
        f"settings fall back to their defaults and cannot be enabled at all — which "
        f"is how the write switch became unreachable."
    )


def test_the_write_switch_is_passed_and_defaults_to_no() -> None:
    """Its own test: this one caused the incident, and it must default OFF."""
    text = COMPOSE.read_text(encoding="utf-8")
    line = next(
        (ln for ln in text.splitlines() if "KHAIBAO9610_ENABLE_REAL_REGISTRATION" in ln),
        None,
    )
    assert line is not None, "compose does not pass KHAIBAO9610_ENABLE_REAL_REGISTRATION"
    assert "${KHAIBAO9610_ENABLE_REAL_REGISTRATION:-no}" in line, (
        f"the write switch must default to 'no' in compose, got: {line.strip()!r}"
    )


@pytest.mark.parametrize("var", ["KHAIBAO9610_MODE", "KHAIBAO9610_ENABLE_REAL_CALLS"])
def test_the_previously_working_switches_are_still_passed(var: str) -> None:
    """Guard the guard: if the extraction stopped finding anything, the test above
    would pass vacuously."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert re.search(rf"{var}\s*:", text), f"{var} is no longer passed by compose"
