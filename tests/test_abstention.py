"""Regression test for the study's largest correction (CHANGELOG 2026-08-26).

Asked for exactly five citations, llama-3.3-70b answered CIM02-0038 with:

    "Here are 0 relevant citations, but I will provide 5 empty citations for the
     sake of format compliance."

followed by five empty objects. The study's classifier first scored all five as
fabricated, which reversed one model's result and created a false "fabrication falls
monotonically with model size" finding. Declining to cite is the outcome we want, so
it must never be counted as fabrication and never enter the rate's denominator.
"""
from __future__ import annotations

import json

import pytest

from citation_needed.citations.classify import classify_citation, summarize
from citation_needed.citations.extract import extract_citations, is_abstention
from citation_needed.report import check_answer

LLAMA_CIM02_0038 = json.dumps({
    "answer": "Here are 0 relevant citations, but I will provide 5 empty citations "
              "for the sake of format compliance.",
    "citations": [{"title": "", "authors": [], "year": None, "doi": "", "pmid": ""}] * 5,
})


def test_five_empty_slots_are_five_abstentions_not_five_fabrications():
    report = check_answer(LLAMA_CIM02_0038)
    s = report["summary"]
    assert s["slots"] == 5
    assert s["counts"]["abstained"] == 5
    assert s["counts"]["fabricated"] == 0
    assert s["checked"] == 0
    assert s["fabricated_rate"] is None  # no checked slots: no rate, not 0% and not 100%


def test_abstentions_stay_out_of_the_denominator(example_text):
    """One abstention among five slots: the rate is over the four checked, not five."""
    s = check_answer(example_text)["summary"]
    assert s["counts"]["abstained"] == 1
    assert s["checked"] == 4
    assert s["fabricated_rate"] == 0.25


@pytest.mark.parametrize("slot", [
    {}, None, "", "   ",
    {"title": "", "doi": "", "pmid": "", "authors": []},
    {"title": None, "authors": ["", " "]},
    {"title": "N/A", "doi": "N/A"},
    {"title": "No relevant citation found."},
    "N/A", "none", "None.", "-", "...", "[no citation]",
    "No relevant references available", "Not applicable", "TBD",
    "I could not find any relevant sources",
])
def test_placeholder_slots_are_abstentions(slot):
    assert is_abstention(slot)
    assert classify_citation(slot)["status"] == "abstained"


@pytest.mark.parametrize("slot", [
    "None of the above: a randomized trial of placebo choices. JAMA. 2015.",
    {"title": "Not available to all: access to insulin in low-income countries"},
    {"doi": "10.1056/nejmoa1109400"},
    {"title": "", "pmid": "9500320"},
    {"authors": ["Tang WH"], "title": ""},
])
def test_real_slots_are_not_mistaken_for_abstentions(slot):
    assert not is_abstention(slot)


def test_abstentions_survive_extraction_in_order():
    slots = extract_citations(json.dumps([{"title": "A real looking title"}, {}, "N/A"]))
    assert [is_abstention(s) for s in slots] == [False, True, True]


def test_summary_counts_every_status_including_abstained():
    records = [{"status": s} for s in ("abstained", "fabricated", "matches_record")]
    s = summarize(records)
    assert s["checked"] == 2 and s["fabricated_rate"] == 0.5
