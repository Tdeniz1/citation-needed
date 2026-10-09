from __future__ import annotations

import json

import pytest

from citation_needed.citations.extract import extract_citations
from citation_needed.citations.parse import parse_citation
from citation_needed.citations.textutil import clean_doi


@pytest.mark.parametrize("text,title", [
    # The study's parser read "SR. M" as three initials and dropped the M (fixed 2026-10-09).
    ("Eurich DT, Weir DL, Majumdar SR. Metformin lowers mortality in heart failure. "
     "N Engl J Med. 2013.", "Metformin lowers mortality in heart failure"),
    ("Smith J. Assessing outcomes in trials. Lancet. 2019.", "Assessing outcomes in trials"),
    ("Tang WH, Wang Z, Levison BS, et al. Intestinal microbial metabolism. NEJM. 2013.",
     "Intestinal microbial metabolism"),
])
def test_title_keeps_its_first_letter(text, title):
    assert parse_citation(text).title == title


def test_identifiers_are_extracted_and_normalized():
    p = parse_citation("Wakefield AJ. Ileal-lymphoid-nodular hyperplasia. Lancet. 1998. "
                       "https://doi.org/10.1016/S0140-6736(97)11096-0. PMID: 9500320")
    assert p.doi == "10.1016/s0140-6736(97)11096-0"  # parens are part of Lancet DOIs
    assert p.pmid == "9500320"
    assert p.year == 1998


def test_wrapping_parens_are_not_part_of_the_doi():
    assert clean_doi("(doi:10.1056/NEJMoa1109400)") == "10.1056/nejmoa1109400"


def test_markdown_reference_section():
    text = ("Claim one [1]. Claim two [2].\n\n## References\n\n"
            "1. Smith J. A first title here. Lancet. 2019.\n"
            "2. Lee K. A second title that wraps\n   onto a second line. BMJ. 2020.\n\n"
            "## Appendix\n\nSome text with 2021 in it. Not a reference. At all here.\n")
    refs = extract_citations(text)
    assert len(refs) == 2
    assert refs[1].endswith("onto a second line. BMJ. 2020.")


def test_bold_references_heading_and_bracket_markers():
    text = "**References:**\n[1] Smith J. Title one. Lancet. 2019.\n[2] Lee K. Title two. BMJ. 2020."
    assert len(extract_citations(text)) == 2


def test_unmarked_one_per_line_references():
    text = "References\nSmith J. Title one is here. Lancet. 2019.\nLee K. Title two is here. BMJ. 2020."
    assert len(extract_citations(text)) == 2


def test_no_reference_section_takes_only_lines_with_a_handle():
    text = ("Statins reduce stroke risk.\n"
            "See Tang WH et al., doi:10.1056/NEJMoa1109400 for details.\n"
            "In 2019 things happened.\n")
    assert extract_citations(text) == ["See Tang WH et al., doi:10.1056/NEJMoa1109400 for details."]


@pytest.mark.parametrize("payload", [
    [{"title": "x"}, {}],
    {"citations": [{"title": "x"}, {}]},
])
def test_json_input(payload):
    assert len(extract_citations(json.dumps(payload))) == 2
    assert len(extract_citations("```json\n" + json.dumps(payload) + "\n```")) == 2
