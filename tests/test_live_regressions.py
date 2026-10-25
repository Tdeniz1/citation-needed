"""Regression tests for false positives found in the live smoke run (2026-10-25).

The answers in examples/live/ were checked against live Crossref and PubMed, and the
responses recorded into tests/fixtures/cache, so these run offline.
"""
from __future__ import annotations

import json

import pytest

from citation_needed.citations import clients
from citation_needed.citations.existence import _match_candidate
from citation_needed.citations.parse import parse_citation
from citation_needed.report import check_answer
from tests.conftest import ROOT

LIVE = ROOT / "examples" / "live"


@pytest.fixture(scope="module")
def diabetes():
    mp = pytest.MonkeyPatch()
    mp.setenv("CITATION_NEEDED_CACHE", str(ROOT / "tests" / "fixtures" / "cache"))
    mp.setenv("CITATION_NEEDED_OFFLINE", "1")
    mp.delenv("CITATION_NEEDED_SOURCES", raising=False)
    yield check_answer((LIVE / "diabetes-answer.md").read_text())["citations"]
    mp.undo()


@pytest.fixture(scope="module")
def llm_json():
    mp = pytest.MonkeyPatch()
    mp.setenv("CITATION_NEEDED_CACHE", str(ROOT / "tests" / "fixtures" / "cache"))
    mp.setenv("CITATION_NEEDED_OFFLINE", "1")
    mp.delenv("CITATION_NEEDED_SOURCES", raising=False)
    yield check_answer((LIVE / "llm-citations-answer.json").read_text())["citations"]
    mp.undo()


def test_diabetes_answer_verdicts(diabetes):
    assert [c["status"] for c in diabetes] == [
        "matches_record",  # Knowler 2002, DPP (real)
        "matches_record",  # Zinman 2015, EMPA-REG (real)
        "matches_record",  # Marso 2016, LEADER, title only (real; was a false `distorted`)
        "fabricated",      # invented intermittent-fasting trial
        "distorted",       # invented title on the EMPA-REG DOI
        "retracted",       # Mehra 2020 hydroxychloroquine registry (retracted)
    ]


def test_title_only_real_paper_resolves_to_itself(diabetes):
    """Was: matched a different 2026 liraglutide paper whose title contains every word
    of the cited title, then graded `distorted` for the year."""
    leader = diabetes[2]
    assert leader["canonical"]["pmid"] == "27295427"
    assert leader["canonical"]["doi"] == "10.1056/nejmoa1603827"


def test_llm_json_verdicts(llm_json):
    assert [c["status"] for c in llm_json] == [
        "matches_record", "matches_record", "fabricated", "matches_record",
        "distorted",  # known limitation, see test below
        "abstained", "abstained",
    ]


def test_pubmed_record_carries_its_own_doi_not_a_cited_reference(llm_json):
    """Was: the PubMed parser kept the last DOI in the whole record, which came from the
    article's reference list (10.1002/bdm.460)."""
    assert llm_json[0]["canonical"]["doi"] == "10.1038/s41598-023-41032-5"


def test_subset_title_needs_a_shared_author():
    """token_set_ratio scores a title 100 against any longer title containing it."""
    parsed = parse_citation(json.loads((LIVE / "llm-citations-answer.json").read_text())["citations"][4])
    correction = {"title": "Correction to: Attention is all you need: utilizing attention in "
                           "AI-enabled drug discovery", "authors": []}
    assert _match_candidate(parsed, correction)[0] is False
    same = {"title": "Attention Is All You Need", "authors": ["vaswani"]}
    assert _match_candidate(parsed, same)[0] is True


def test_known_limitation_redeposited_record_reads_as_year_distortion(llm_json):
    """'Attention is all you need' (NeurIPS 2017) has no Crossref or PubMed record of its
    own. Crossref holds 2025 re-deposits with the real title and authors, so the year
    grade fires. Documented in the README; this test pins the behavior so a change to
    it is deliberate."""
    rec = llm_json[4]
    assert rec["canonical"]["title"] == "Attention Is All You Need"
    assert rec["canonical"]["year"] == 2025
    assert rec["reason"] == "Materially wrong: year."


def test_pubmed_parser_ignores_reference_list_ids():
    import xml.etree.ElementTree as ET
    xml = """<PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>T</ArticleTitle>
    </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/own</ArticleId>
    </ArticleIdList><ReferenceList><Reference><ArticleIdList><ArticleId IdType="doi">10.1/cited</ArticleId>
    <ArticleId IdType="pmc">PMC9</ArticleId></ArticleIdList></Reference></ReferenceList></PubmedData>
    </PubmedArticle>"""
    rec = clients._pubmed_parse_article(ET.fromstring(xml))
    assert rec["doi"] == "10.1/own" and rec["pmcid"] is None
