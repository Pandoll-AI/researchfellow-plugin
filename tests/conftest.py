"""Shared pytest fixtures for the ResearchFellow trust-foundation suite.

Stdlib-first: the state / PHI / DSL / drift tests need only pytest. Tests that
exercise real-data model fitting are skipped when the runtime stats stack
(requirements.txt) is absent — see `has_stats_stack`.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "researchfellow" / "scripts"
REFERENCES = ROOT / "skills" / "researchfellow" / "references"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

# Make the plugin scripts importable as top-level modules (state_tool, etc.).
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="session")
def scripts_dir() -> pathlib.Path:
    return SCRIPTS


@pytest.fixture(scope="session")
def references_dir() -> pathlib.Path:
    return REFERENCES


@pytest.fixture(scope="session")
def fixtures_dir() -> pathlib.Path:
    return FIXTURES


@pytest.fixture(scope="session")
def run_script():
    """Return a callable that runs a plugin script as a subprocess.

    Subprocess (not import) is deliberate for the CLI tools: exit codes ARE the
    contract for state_tool / analysis_runner, and SystemExit is cleanest to
    assert across a process boundary.
    """

    def _run(script_name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script_name), *args],
            capture_output=True,
            text=True,
        )

    return _run


def _stats_stack_available() -> bool:
    try:
        import pandas  # noqa: F401
        import statsmodels.api  # noqa: F401
        import lifelines  # noqa: F401
    except ImportError:
        return False
    return True


HAS_STATS_STACK = _stats_stack_available()

requires_stats_stack = pytest.mark.skipif(
    not HAS_STATS_STACK,
    reason="real-data model fitting needs pandas+statsmodels+lifelines (requirements.txt)",
)
