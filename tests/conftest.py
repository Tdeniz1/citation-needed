"""Every test runs offline against recorded API responses in tests/fixtures/cache.

A cache miss becomes `could_not_check`, never a network call, so CI is deterministic
and never depends on Crossref or PubMed being up. To re-record after changing what
the checker asks for: delete tests/fixtures/cache and run
`CITATION_NEEDED_CACHE=tests/fixtures/cache citation-needed examples/answer.md`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CACHE = ROOT / "tests" / "fixtures" / "cache"
EXAMPLE = ROOT / "examples" / "answer.md"


@pytest.fixture(autouse=True)
def offline_fixture_cache(monkeypatch):
    monkeypatch.setenv("CITATION_NEEDED_CACHE", str(FIXTURE_CACHE))
    monkeypatch.setenv("CITATION_NEEDED_OFFLINE", "1")
    monkeypatch.delenv("CITATION_NEEDED_SOURCES", raising=False)
    monkeypatch.delenv("CITATION_NEEDED_EMAIL", raising=False)


@pytest.fixture(scope="session")
def example_text() -> str:
    return EXAMPLE.read_text()
