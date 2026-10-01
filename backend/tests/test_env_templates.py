"""The environment templates must not lie.

A configuration template is the thing an operator follows. If it lists a key the
application never reads, the operator sets it, believes something was configured,
and nothing happened. That is a silent failure with a human in the loop, which is
the worst kind: the person has positive evidence they did the right thing.

This file exists because that had already happened. ``deploy/env.production.example``
carried **eight** keys nothing read — including:

* ``SECRET_KEY`` — invited someone to generate and carefully guard a secret that
  did nothing;
* ``RATE_LIMIT_REGISTRATIONS`` — the name the application reads is
  ``RATE_LIMIT_ATTEMPTS``, so anyone tuning the registration limit would have
  silently kept the default;
* ``LEAD_RETENTION_DAYS``, ``DB_POOL_SIZE``, ``DB_MAX_OVERFLOW``,
  ``KHAIBAO9610_API_KEY``, ``LOG_LEVEL``, ``SENTRY_DSN`` — all unimplemented.

and it was **missing** ``AUTO_CREATE_SCHEMA``, whose default is ``true``, so a
deployment following the template would have created its tables with
``create_all`` and bypassed Alembic entirely.

Two tests, deliberately separate:

1. the key set must equal ``Settings.model_fields`` — no orphans, none missing;
2. the template's *safety posture* is pinned, so the values that matter cannot be
   changed by accident either.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

TEMPLATES = {
    "repo-root .env.example": ROOT / ".env.example",
    "deploy/env.production.example": ROOT / "deploy" / "env.production.example",
}

_KEY_RE = re.compile(r"(?m)^([A-Z][A-Z0-9_]*)\s*=")


def keys_of(path: Path) -> set[str]:
    return set(_KEY_RE.findall(path.read_text(encoding="utf-8")))


def values_of(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k and k.isupper():
            out[k] = v.strip()
    return out


def settings_fields() -> set[str]:
    from app.config import Settings

    return {name.upper() for name in Settings.model_fields}


@pytest.mark.parametrize("label", sorted(TEMPLATES))
def test_template_has_no_orphan_keys_and_no_missing_settings(label: str) -> None:
    """Every documented key must be read, and every setting must be documented."""
    path = TEMPLATES[label]
    assert path.is_file(), f"{label} not found at {path}"

    documented = keys_of(path)
    real = settings_fields()

    orphans = sorted(documented - real)
    missing = sorted(real - documented)

    assert not orphans, (
        f"{label} documents {len(orphans)} key(s) the application never reads: "
        f"{orphans}. An operator would set these and nothing would happen. "
        f"Either implement them or delete them from the template."
    )
    assert not missing, (
        f"{label} does not document {len(missing)} setting(s): {missing}. "
        f"An operator cannot configure what is not written down."
    )


def test_production_template_pins_the_safety_posture() -> None:
    """The values that matter, not just the key names.

    Each of these is a control. If one is edited, that is a decision that should
    have to be made deliberately rather than by hand-editing a template.
    """
    v = values_of(TEMPLATES["deploy/env.production.example"])

    # Migrations, not create_all. The default is true, so this line is load-bearing.
    assert v.get("AUTO_CREATE_SCHEMA") == "false", (
        "production must run `alembic upgrade head`, not create_all"
    )

    # Registration must not talk to an unverified external API by default.
    assert v.get("KHAIBAO9610_MODE") == "mock", (
        "production template must default to the mock provider; the real contract "
        "is not verified (issue #4)"
    )
    assert v.get("KHAIBAO9610_ENABLE_REAL_CALLS") == "no", (
        "the second opt-in switch must default to no, so a real call cannot happen by accident"
    )

    # Same-origin only.
    assert v.get("CORS_ALLOW_ORIGINS", "") == "", (
        "CORS must default to disabled; this site is same-origin"
    )

    # API docs hidden unless deliberately exposed.
    assert v.get("ENABLE_API_DOCS", "") == "", (
        "API docs must default to unset so they are hidden in production"
    )

    # No credential may be committed with a value.
    for key in ("ADMIN_API_TOKEN", "DATABASE_URL"):
        assert key in v, f"{key} must be documented"


def test_no_tracked_template_contains_a_filled_secret() -> None:
    """A template is a template. Values stay empty for anything credential-like."""
    credentialish = (
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "API_KEY",
        "DSN",
        "PRIVATE",
    )
    for label, path in TEMPLATES.items():
        for key, value in values_of(path).items():
            if any(word in key for word in credentialish):
                assert value == "", (
                    f"{label}: {key} has a non-empty value. A credential in a "
                    f"tracked template is a leaked credential."
                )


# ---------------------------------------------------------------------------
# The containerised deployment is a third place a key can go stale.
# ---------------------------------------------------------------------------

COMPOSE = ROOT / "deploy" / "docker-compose.yml"


def _effective(value: str) -> str:
    """Resolve a compose ``${VAR:-default}`` expression to its default.

    Compose interpolation means the literal text in the file is not the value the
    container sees. ``KHAIBAO9610_MODE: ${KHAIBAO9610_MODE:-mock}`` resolves to
    ``mock`` when the host variable is unset — so the assertion has to be about
    the DEFAULT, not the raw string. Comparing the raw string would have failed
    against a perfectly safe configuration, which is a test lying in the
    alarming direction.
    """
    text = value.strip()
    m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}", text)
    if not m:
        return text
    return (m.group(2) or "").strip()


def _compose_app_environment() -> dict[str, str]:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    env = data["services"]["app"]["environment"]
    # Compose accepts both a mapping and a list of KEY=VALUE strings.
    if isinstance(env, list):
        out = {}
        for item in env:
            key, _, value = str(item).partition("=")
            out[key] = value
        return out
    return {str(k): str(v) for k, v in env.items()}


def test_compose_environment_uses_only_real_settings() -> None:
    """Same rule as the templates: no key the application never reads."""
    env = _compose_app_environment()
    orphans = sorted(set(env) - settings_fields())
    assert not orphans, (
        f"deploy/docker-compose.yml sets {len(orphans)} key(s) the application "
        f"never reads: {orphans}"
    )


def test_compose_pins_the_safety_posture() -> None:
    env = {k: _effective(v) for k, v in _compose_app_environment().items()}
    assert env.get("AUTO_CREATE_SCHEMA") in ("false", "False", "0"), (
        "the compose service must not create tables with create_all; the stack "
        "runs `alembic upgrade head` as a separate step"
    )
    assert env.get("KHAIBAO9610_MODE", "mock") == "mock"
    assert env.get("KHAIBAO9610_ENABLE_REAL_CALLS", "no") == "no"
    assert env.get("CORS_ALLOW_ORIGINS", "") == ""
