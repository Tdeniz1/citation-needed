# Ported from github.com/Tdeniz1/Open-Weight-LLM-Medical-Citation-Audit (MIT, Deniz Turk 2026),
# code/harness/retraction.py. Logic unchanged unless a comment says otherwise.
"""Step 3: retraction check.

Flag Retracted when ANY of these fire, and record which source(s) fired:
  * Crossref update-to relation of type "retraction" (or a relation key naming a
    retraction), on the resolved work.
  * OpenAlex is_retracted flag.
  * PubMed publication type "Retracted Publication" (or "Retraction of Publication").
  * Presence of the DOI/PMID as an OriginalPaper in the Retraction Watch dataset with
    RetractionNature == "Retraction".

Expression-of-Concern and Correction notices are recorded separately (they are not a
retraction) so the taxonomy stays clean.
"""
from __future__ import annotations

import csv
import functools

from citation_needed import config
from citation_needed.citations import clients
from citation_needed.citations.textutil import clean_doi

# --------------------------------------------------------------------------- #
# Retraction Watch dataset (local CSV, indexed once).                          #
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def _load_retraction_watch() -> dict:
    """Return {'by_doi': {...}, 'by_pmid': {...}} indexes over the RW dump.

    Each value is a dict with nature/reason/retraction_doi so the harness can report
    exactly what RW says. Only rows whose OriginalPaper identifiers are present index.
    """
    by_doi: dict[str, dict] = {}
    by_pmid: dict[str, dict] = {}
    csv_path = config.retraction_watch_csv()
    if csv_path is None or not csv_path.exists():
        return {"by_doi": by_doi, "by_pmid": by_pmid, "loaded": False}
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            nature = (row.get("RetractionNature") or "").strip()
            rec = {
                "nature": nature,
                "reason": (row.get("Reason") or "").strip(),
                "retraction_doi": clean_doi(row.get("RetractionDOI")),
                "retraction_date": (row.get("RetractionDate") or "").strip(),
                "record_id": row.get("Record ID"),
            }
            doi = clean_doi(row.get("OriginalPaperDOI"))
            pmid = (row.get("OriginalPaperPubMedID") or "").strip()
            if doi:
                by_doi[doi] = rec
            if pmid and pmid.isdigit() and pmid != "0":
                by_pmid[pmid] = rec
    return {"by_doi": by_doi, "by_pmid": by_pmid, "loaded": True}


def _rw_lookup(doi: str | None, pmid: str | None) -> dict | None:
    idx = _load_retraction_watch()
    if doi:
        hit = idx["by_doi"].get(clean_doi(doi))
        if hit:
            return hit
    if pmid:
        hit = idx["by_pmid"].get(str(pmid).strip())
        if hit:
            return hit
    return None


# --------------------------------------------------------------------------- #
# Crossref update-to inspection (re-fetch the work if we only had a title match) #
# --------------------------------------------------------------------------- #

def _crossref_retraction_relation(canonical: dict) -> bool:
    update_to = canonical.get("crossref_update_to") or []
    for u in update_to:
        if (u.get("type") or "").lower() == "retraction":
            return True
    for key in canonical.get("crossref_relation") or []:
        if "retract" in key.lower():
            return True
    return False


def check_retraction(canonical: dict) -> dict:
    doi = canonical.get("doi")
    pmid = canonical.get("pmid")
    sources_fired: list[str] = []
    detail: dict = {}

    # OpenAlex flag (may already be on the merged canonical record). Live OpenAlex
    # lookups are disabled by default since 2026-08-18 (metered API — see config);
    # a flag already present on the canonical record is still honoured.
    is_retr = canonical.get("is_retracted")
    if is_retr is None and doi and "openalex" in config.existence_sources():
        oa = clients.openalex_by_id(doi=doi)
        is_retr = oa.get("is_retracted") if oa else None
    if is_retr:
        sources_fired.append("openalex")

    # Crossref update-to relation. Ensure we have the full Crossref record.
    cr = None
    src_recs = canonical.get("all_source_records") or {}
    if "crossref" in src_recs:
        cr = src_recs["crossref"]
    elif doi:
        cr = clients.crossref_by_doi(doi)
    if cr and _crossref_retraction_relation(cr):
        sources_fired.append("crossref")

    # PubMed publication type
    pub_types = []
    if "pubmed" in src_recs:
        pub_types = src_recs["pubmed"].get("pub_types") or []
    elif pmid:
        pm = clients.pubmed_efetch(pmid)
        if pm:
            pub_types = pm.get("pub_types") or []
    pt_lower = {p.lower() for p in pub_types}
    if "retracted publication" in pt_lower or "retraction of publication" in pt_lower:
        sources_fired.append("pubmed")

    # Retraction Watch dataset
    rw = _rw_lookup(doi, pmid)
    if rw and rw["nature"].lower() == "retraction":
        sources_fired.append("retraction_watch")
        detail["retraction_watch"] = rw
    elif rw:
        # notice exists but is a correction / EoC, not a retraction
        detail["retraction_watch_nonretraction"] = rw

    return {
        "retracted": bool(sources_fired),
        "sources_fired": sorted(set(sources_fired)),
        "detail": detail,
    }
