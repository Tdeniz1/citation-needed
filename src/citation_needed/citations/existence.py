# Ported from github.com/Tdeniz1/Open-Weight-LLM-Medical-Citation-Audit (MIT, Deniz Turk 2026),
# code/harness/existence.py. Logic unchanged unless a comment says otherwise.
"""Step 2: multi-source existence check.

Resolve any DOI (Crossref + OpenAlex) and any PMID (PubMed + OpenAlex) directly.
Independently match the parsed title + authors against Crossref, PubMed, OpenAlex,
and Semantic Scholar using a normalized title-similarity threshold near 0.9 combined
with author-surname overlap.

Fabricated is returned ONLY when the DOI and PMID both fail to resolve AND no index
returns a title match above threshold. Multi-source matching means a single API miss
never yields a false Fabricated label (brief requirement).
"""
from __future__ import annotations

from citation_needed import config
from citation_needed.citations import clients
from citation_needed.citations.parse import ParsedCitation
from citation_needed.citations.textutil import (
    author_overlap,
    surname,
    title_similarity,
    title_similarity_full,
)
from citation_needed.config import MIN_AUTHOR_OVERLAP, TITLE_SIM_STRONG, TITLE_SIM_THRESHOLD


def _match_candidate(parsed: ParsedCitation, cand: dict) -> tuple[bool, float, int, float]:
    """Return (is_match, title_sim, author_overlap, full_title_sim) for one candidate.

    Changed from the study: token_set_ratio scores 100 whenever one title's words are a
    subset of the other's, so a short real title used to match any longer paper that
    contained it, with no author check (live false positives: "Attention is all you
    need" matched a correction notice; "Liraglutide and cardiovascular outcomes in type
    2 diabetes" matched a different liraglutide paper). When the whole titles are not
    close, the match now needs a shared author whenever the citation names authors.
    """
    sim = title_similarity(parsed.title, cand.get("title"))
    full = title_similarity_full(parsed.title, cand.get("title"))
    ov = author_overlap(parsed.authors, cand.get("authors") or [])
    if full < TITLE_SIM_THRESHOLD and parsed.authors and ov < MIN_AUTHOR_OVERLAP:
        return False, sim, ov, full   # subset-only title match, and no shared author
    if sim >= TITLE_SIM_STRONG:
        return True, sim, ov, full    # near-identical title alone is enough
    if sim >= TITLE_SIM_THRESHOLD and (ov >= MIN_AUTHOR_OVERLAP or not parsed.authors):
        return True, sim, ov, full
    return False, sim, ov, full


def check_existence(parsed: ParsedCitation) -> dict:
    result = {
        "resolved": False,
        "id_resolved": False,
        "title_matched": False,
        "matched_sources": [],
        "canonical": None,
        "candidates": [],
        "best_title_sim": 0.0,
        "doi": parsed.doi,
        "pmid": parsed.pmid,
    }
    canonical_by_source: dict[str, dict] = {}

    # -- direct identifier resolution -------------------------------------- #
    # Source roster is config-driven since 2026-08-18: OpenAlex went metered and S2's
    # shared pool throttles hard, so the default is Crossref+PubMed (see config.py for
    # the empirical 1.4%/0% sole-resolver bounds that justify this).
    EXISTENCE_SOURCES = config.existence_sources()
    if parsed.doi:
        if "crossref" in EXISTENCE_SOURCES:
            cr = clients.crossref_by_doi(parsed.doi)
            if cr and cr["raw_ok"]:
                canonical_by_source["crossref"] = cr
        if "openalex" in EXISTENCE_SOURCES:
            oa = clients.openalex_by_id(doi=parsed.doi)
            if oa and oa["raw_ok"]:
                canonical_by_source["openalex"] = oa
        if "s2" in EXISTENCE_SOURCES:
            s2 = clients.s2_by_id(doi=parsed.doi)
            if s2 and s2["raw_ok"]:
                canonical_by_source["s2"] = s2
    if parsed.pmid:
        if "pubmed" in EXISTENCE_SOURCES:
            pm = clients.pubmed_efetch(parsed.pmid)
            if pm and pm["raw_ok"]:
                canonical_by_source["pubmed"] = pm
        if "openalex" in EXISTENCE_SOURCES and "openalex" not in canonical_by_source:
            oa = clients.openalex_by_id(pmid=parsed.pmid)
            if oa and oa["raw_ok"]:
                canonical_by_source["openalex"] = oa

    if canonical_by_source:
        result["id_resolved"] = True
        result["matched_sources"] = sorted(canonical_by_source)

    # -- independent title/author matching across all four indices --------- #
    if parsed.title:
        all_searchers = {
            "crossref": lambda t: clients.crossref_search(t, parsed.author_strings[0] if parsed.author_strings else None),
            "pubmed": lambda t: clients.pubmed_search_title(
                t, surname(parsed.author_strings[0]) if parsed.author_strings else None),
            "openalex": clients.openalex_search,
            "s2": clients.s2_search,
        }
        searchers = {s: fn for s, fn in all_searchers.items() if s in EXISTENCE_SOURCES}
        for src, fn in searchers.items():
            try:
                cands = fn(parsed.title)
            except Exception:  # a single index failing must not fail the check
                cands = []
            # Changed from the study: take the best matching candidate per source, not the
            # first one (closest whole title, then most shared authors, then closest year), so a
            # looser match ranked higher by the index cannot win.
            best = None
            for cand in cands:
                is_match, sim, ov, full = _match_candidate(parsed, cand)
                result["best_title_sim"] = max(result["best_title_sim"], sim)
                year_gap = abs((cand.get("year") or 0) - parsed.year) if parsed.year and cand.get("year") else 99
                key = (full, ov, -year_gap)
                if is_match and (best is None or key > best[0]):
                    best = (key, cand, sim, ov)
            if best:
                _, cand, sim, ov = best
                result["title_matched"] = True
                if src not in canonical_by_source:
                    canonical_by_source[src] = cand
                if src not in result["matched_sources"]:
                    result["matched_sources"].append(src)
                result["candidates"].append(
                    {"source": src, "title": cand.get("title"), "sim": sim,
                     "author_overlap": ov, "doi": cand.get("doi"), "pmid": cand.get("pmid")}
                )

    result["resolved"] = bool(canonical_by_source)
    if result["resolved"]:
        # Prefer PubMed > Crossref > OpenAlex > S2 as the canonical record, but
        # merge identifiers so retraction/support checks have DOI+PMID+PMCID.
        order = ["pubmed", "crossref", "openalex", "s2"]
        primary_src = next((s for s in order if s in canonical_by_source), None)
        canonical = dict(canonical_by_source[primary_src])
        for s in order:
            rec = canonical_by_source.get(s)
            if not rec:
                continue
            for k in ("doi", "pmid", "pmcid"):
                if not canonical.get(k) and rec.get(k):
                    canonical[k] = rec[k]
            if canonical.get("is_retracted") is None and rec.get("is_retracted") is not None:
                canonical["is_retracted"] = rec["is_retracted"]
        canonical["all_source_records"] = canonical_by_source
        result["canonical"] = canonical
        result["matched_sources"] = sorted(set(result["matched_sources"]) | set(canonical_by_source))

    return result
