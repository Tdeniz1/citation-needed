"""End-to-end checks against recorded Crossref/PubMed responses, plus the taxonomy
invariants the study enforced on all 43,508 of its records."""
from __future__ import annotations

import pytest

from citation_needed.citations.classify import STATUSES, classify_citation
from citation_needed.cli import main
from citation_needed.report import check_answer, to_json, to_markdown

EXPECTED = ["matches_record", "retracted", "distorted", "fabricated", "abstained"]


@pytest.fixture(scope="module")
def report():
    from tests.conftest import EXAMPLE, FIXTURE_CACHE  # module fixture: set env by hand
    mp = pytest.MonkeyPatch()
    mp.setenv("CITATION_NEEDED_CACHE", str(FIXTURE_CACHE))
    mp.setenv("CITATION_NEEDED_OFFLINE", "1")
    mp.delenv("CITATION_NEEDED_SOURCES", raising=False)
    yield check_answer(EXAMPLE.read_text())
    mp.undo()


def test_each_example_citation_gets_the_right_status(report):
    assert [r["status"] for r in report["citations"]] == EXPECTED


def test_distorted_shows_the_paper_the_doi_really_points_to(report):
    """Existence is not correctness: the DOI resolves, to the wrong paper."""
    rec = report["citations"][2]
    assert rec["existence"]["id_resolved"] is True
    assert rec["parsed"]["title"].startswith("Metformin")
    assert "phosphatidylcholine" in rec["canonical"]["title"].lower()
    assert "different real paper" in rec["reason"]


def test_retraction_names_its_evidence(report):
    rec = report["citations"][1]
    assert rec["retraction"]["sources_fired"]


def test_cache_miss_is_could_not_check_not_fabricated():
    """An unreachable index is not evidence that a paper does not exist."""
    rec = classify_citation("Nobody A. A title this cache has never seen before. 2020. "
                            "doi:10.9999/never-cached")
    assert rec["status"] == "could_not_check"
    assert rec["lookup_failures"]


def test_citation_with_nothing_to_look_up_is_could_not_check():
    assert classify_citation({"authors": ["Smith J"], "year": 2019})["status"] == "could_not_check"


# --- invariants (ported from the study's tests/test_taxonomy_invariants.py) --- #

def test_status_vocabulary_is_closed(report):
    assert {r["status"] for r in report["citations"]} <= set(STATUSES)


def test_fabricated_means_nothing_resolved_and_nothing_failed(report):
    for r in report["citations"]:
        if r["status"] == "fabricated":
            assert r["existence"]["resolved"] is False
            assert not r["existence"]["matched_sources"]
            assert not r["lookup_failures"]


def test_retracted_and_distorted_require_a_resolved_record(report):
    for r in report["citations"]:
        if r["status"] in ("retracted", "distorted"):
            assert r["existence"]["resolved"] is True
            assert r["canonical"]


def test_distorted_is_a_major_metadata_grade_only(report):
    for r in report["citations"]:
        if r["status"] == "distorted":
            assert r["metadata"]["overall"] == "major"
            assert r["retraction"]["retracted"] is False


def test_matches_record_is_never_silently_supported(report):
    """Without the judge, nothing may claim the source supports the claim."""
    for r in report["citations"]:
        assert r["support"] is None
        if r["status"] == "matches_record":
            assert r["metadata"]["overall"] in ("none", "minor")


def test_reports_render(report):
    md = to_markdown(report)
    assert "[citation needed]" in md and "Resolved:" in md
    assert '"status": "distorted"' in to_json(report)


def test_cli_fail_on_gates_the_exit_code(tmp_path, capsys):
    from tests.conftest import EXAMPLE
    assert main([str(EXAMPLE)]) == 0
    assert main([str(EXAMPLE), "--fail-on", "fabricated"]) == 1
    clean = tmp_path / "clean.md"
    clean.write_text("## References\n\n1. N/A\n")
    assert main([str(clean), "--fail-on", "fabricated,distorted"]) == 0
    capsys.readouterr()
