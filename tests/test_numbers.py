"""Numbers checker: every number in the answer must appear in the supplied sources."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from citation_needed.cli import main
from citation_needed.numbers import SourceDoc, check_numbers, extract_numbers, load_sources

ROOT = Path(__file__).resolve().parent.parent


def status(answer: str, source: str, **kw) -> list[tuple[str, str]]:
    report = check_numbers(answer, [SourceDoc("src.md", source)], **kw)
    return [(r["text"], r["status"]) for r in report["numbers"]]


def one(answer: str, source: str, **kw) -> str:
    got = status(answer, source, **kw)
    assert len(got) == 1, got
    return got[0][1]


# --- normalization ---------------------------------------------------------- #

@pytest.mark.parametrize("written", ["43,508", "43508", "43 508", "43 508"])
def test_thousands_separators_normalize(written):
    assert one(f"We scored {written} citations.", "a total of 43,508 citations") == "found"


@pytest.mark.parametrize("written", ["7.82 percent", "7.82 per cent", "7.82 %"])
def test_percent_spellings_match(written):
    assert one(f"Fabrication was {written}.", "fabrication 7.82% overall") == "found"


@pytest.mark.parametrize("answer,source", [
    ("1.5 million patients", "1,500,000 patients"),
    ("models from 8B parameters", "an 8 billion parameter model"),
    ("3.5k downloads", "3,500 downloads"),
    ("$1.2 million", "US$1,200,000"),
])
def test_scale_words_and_suffixes(answer, source):
    assert one(answer, source) == "found"


def test_sign_is_ignored_for_relative_changes():
    assert one("fabrication fell 79.1%", "a change of −79.1% versus baseline") == "found"


def test_leading_decimal_point():
    assert one("P < 0.001", "Holm-adjusted P < .001") == "found"


# --- rounding and hedges ------------------------------------------------------ #

def test_rounding_to_fewer_decimals_is_rounded():
    assert one("about 7.8% fabricated", "7.82%") == "approximate"
    assert one("7.8% fabricated", "7.82%") == "rounded"
    assert one("8% fabricated", "7.82%") == "rounded"


def test_unhedged_round_integer_is_not_a_rounding():
    """'1,000 patients' is a precise claim; the source said 1,024."""
    assert one("1,000 patients", "1,024 patients") == "not_found"
    assert one("about 1,000 patients", "1,024 patients") == "approximate"


def test_more_precise_answer_than_source_is_not_found():
    assert one("7.82%", "about 8%") == "not_found"


def test_about_tolerance_is_bounded():
    assert one("about 20% of responses", "19.6% of responses") == "approximate"
    assert one("about 30% of responses", "19.6% of responses") == "not_found"


def test_bounds_must_be_on_the_right_side():
    assert one("more than 40,000 citations", "43,508 citations") == "approximate"
    assert one("fewer than 40,000 citations", "43,508 citations") == "not_found"
    assert one("up to 50 participants", "48 participants") == "approximate"


# --- ranges ----------------------------------------------------------------- #

def test_range_needs_both_ends():
    src = "rates between 10% and 20%"
    assert one("10-20% of answers", src) == "found"
    report = check_numbers("10-25% of answers", [SourceDoc("s", src)])
    rec = report["numbers"][0]
    assert rec["kind"] == "range" and rec["status"] == "not_found"
    assert "25%" in rec["reason"]


@pytest.mark.parametrize("answer", ["between 5 and 10 days", "5 to 10 days", "5–10 days"])
def test_range_spellings(answer):
    mentions = extract_numbers(answer)
    assert len(mentions) == 1 and mentions[0].kind == "range"
    assert [str(e.value) for e in mentions[0].endpoints] == ["5", "10"]
    assert all(e.unit == "day" for e in mentions[0].endpoints)


def test_scale_suffix_ranges():
    assert one("models 8B to 120B", "8-billion and 120-billion parameter models") == "found"
    assert one("1-2 million users", "between 1 million and 2 million users") == "found"


# --- units ------------------------------------------------------------------ #

def test_exact_unit_conversion():
    assert one("a 0.5 g dose", "500 mg daily") == "found"
    assert one("for 2 hours", "infused over 120 minutes") == "found"


def test_conflicting_unit_is_flagged():
    assert one("a 5 mg dose", "a 5 g dose") == "unit_mismatch"
    assert one("20% of patients", "after 20 days") == "unit_mismatch"


def test_unitless_side_is_compatible():
    assert one("a 500 mg dose", "dose (mg): 500") == "found"


# --- what is not a number to check -------------------------------------------- #

@pytest.mark.parametrize("text", [
    "COVID-19 vaccines", "qwen3.6-27b", "H2O and CO2", "see Figure 2 and Table 3",
    "as shown before [3]", "as shown [2, 4-6]", "doi:10.1056/NEJMoa1109400",
    "https://example.org/page/42", "PMID: 9500320", "a 3D model", "the 4th trial",
    "Tang et al., 2013", "(Eurich, 2013)", "version 3.6.1", "`n_jobs=8`",
])
def test_identifiers_and_scaffolding_are_skipped(text):
    assert extract_numbers(text) == []


def test_reference_list_and_list_numbering_are_skipped():
    answer = ("Key points:\n\n1. Fabrication was 7.82%.\n2. Retraction was rare.\n\n"
              "## References\n\n1. Tang WH. N Engl J Med. 2013;368(17):1575-1584.\n")
    assert [m.text for m in extract_numbers(answer)] == ["7.82%"]


def test_dates_must_match_as_written():
    assert one("released on 2026-08-20", "date-released: 2026-08-20") == "found"
    assert one("released on 2026-08-21", "date-released: 2026-08-20") == "not_found"


def test_years_are_checked_unless_ignored():
    assert one("In 2013, a trial ran.", "no year here") == "not_found"
    report = check_numbers("In 2013, a trial ran.", [SourceDoc("s", "x")], ignore_years=True)
    assert report["numbers"] == [] and report["summary"]["ignored_years"] == 1


def test_number_words():
    # read from sources by default
    assert one("3 conditions", "three conditions") == "found"
    # read from the answer only when asked: "one of the" is prose, not a claim
    assert status("one of the three trials", "x") == []
    assert one("three trials", "3 trials", words=True) == "found"


# --- end to end ---------------------------------------------------------------- #

def test_example_flags_exactly_the_superseded_numbers():
    """examples/numbers-answer.md repeats three figures the study later corrected."""
    docs, skipped = load_sources([ROOT / "examples" / "sources"])
    assert not skipped
    report = check_numbers((ROOT / "examples" / "numbers-answer.md").read_text(), docs)
    flagged = [r["text"] for r in report["numbers"] if r["status"] == "not_found"]
    assert flagged == ["43,631", "2.14", "76.4%"]
    assert report["summary"]["flagged"] == 3


def test_deterministic():
    docs, _ = load_sources([ROOT / "examples" / "sources"])
    text = (ROOT / "examples" / "numbers-answer.md").read_text()
    assert check_numbers(text, docs) == check_numbers(text, docs)


def test_evidence_points_at_the_source_line():
    report = check_numbers("x\nrate 7.82%", [SourceDoc("doc.md", "intro\n\nrate was 7.82% here")])
    rec = report["numbers"][0]
    assert rec["line"] == 2
    assert rec["evidence"][0] == {"source": "doc.md", "line": 3, "text": "7.82%",
                                  "context": "rate was 7.82% here"}


def test_load_sources_reads_dirs_html_and_reports_skips(tmp_path):
    (tmp_path / "a.md").write_text("value 1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.html").write_text("<p>value <b>2</b></p><script>var x = 99;</script>")
    (tmp_path / ".hidden.txt").write_text("value 3")
    (tmp_path / "c.docx").write_bytes(b"\x00")
    docs, skipped = load_sources([tmp_path, tmp_path / "missing.txt"])
    assert [Path(d.name).name for d in docs] == ["a.md", "b.html"]
    assert "99" not in docs[1].text and "2" in docs[1].text
    assert any("c.docx" in s for s in skipped) and any("not found" in s for s in skipped)


def test_cli_numbers_only(tmp_path, capsys):
    answer = ROOT / "examples" / "numbers-answer.md"
    sources = str(ROOT / "examples" / "sources")
    assert main([str(answer), "--sources", sources, "--only", "numbers"]) == 0
    md = capsys.readouterr().out
    assert "## Numbers" in md and "[number needed]" in md and "## Citations" not in md
    assert main([str(answer), "--sources", sources, "--only", "numbers",
                 "--fail-on", "not_found"]) == 1
    capsys.readouterr()
    assert main([str(answer), "--sources", sources, "--only", "numbers", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["numbers"]["summary"]["flagged"] == 3 and "citations" not in data


def test_cli_both_checks(capsys):
    from tests.conftest import EXAMPLE
    assert main([str(EXAMPLE), "--sources", str(EXAMPLE), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["citations"] and data["numbers"]["summary"]["flagged"] == 0


def test_cli_only_numbers_requires_sources(capsys):
    with pytest.raises(SystemExit):
        main([str(ROOT / "examples" / "answer.md"), "--only", "numbers"])
    capsys.readouterr()
