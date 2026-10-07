"""Deploy-script test suite, driven from pytest.

The measurements themselves live in ``deploy/tests/thu-deploy.sh`` — what is
under test is bash, so the test is bash. This file exists so the suite sits in
the default ``pytest`` path: a test suite outside the default path is a test
suite that rots without anyone noticing.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SUITE = REPO / "deploy" / "tests" / "thu-deploy.sh"

SCRIPTS = ["common.sh", "staging.sh", "production.sh", "rollback.sh", "backup.sh", "verify.sh"]


def test_deploy_suite_passes() -> None:
    assert SUITE.is_file(), f"missing {SUITE}"
    r = subprocess.run(["bash", str(SUITE)], cwd=REPO, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        pytest.fail(f"deploy suite FAILED (exit {r.returncode}):\n{r.stdout}\n{r.stderr}")
    # Assert it actually ran cases rather than exiting early with none.
    assert "KẾT QUẢ:" in r.stdout
    assert " 0 không đạt" in r.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_syntax(script: str) -> None:
    p = REPO / "deploy" / script
    assert p.is_file(), f"missing {p}"
    r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, f"{script} syntax error:\n{r.stderr}"
