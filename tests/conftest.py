from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_agent.store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    db = tmp_path / "test.db"
    s = Store(db)
    s.seed_from_fixtures(fixture_dir=ROOT / "data", reset_operational=True)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _disable_langsmith_in_unit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must stay offline and must not upload traces."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
