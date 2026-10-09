"""Classify one citation slot. Ported from the study's harness/classify.py.

Exactly one status per slot, decided in this order:

  abstained        the slot is empty or a placeholder ("N/A", {}). The model declined
                   to cite, which is the good outcome. Never counted as fabricated.
  could_not_check  nothing resolved, but some lookup failed (network, rate limit,
                   offline cache miss) or the slot has no title and no identifier.
                   We do not know, so we do not guess.
  fabricated       every lookup answered, and no index knows this work.
  retracted        the work exists and at least one retraction signal fired.
  distorted        the work exists, is not retracted, and the metadata is materially
                   wrong: usually a real DOI or PMID attached to a different paper.
  matches_record   the work exists, is not retracted, and the metadata matches.
                   This says nothing about whether the paper supports the claim.

With the EXPERIMENTAL support judge enabled, a matches_record slot may instead come
back `supported` or `unsupported`. The judge failed its human-agreement gate in the
study (Cohen's kappa 0.525 on 100 blind pairs), so treat those labels as a hint.

Status names differ from the study's: its `clean_metadata_support_pending` is
`matches_record` here, `valid` is `supported`, `clean_but_unsupported` is
`unsupported`. `abstained` and `could_not_check` are new.
"""
from __future__ import annotations

from citation_needed import cache, config
from citation_needed.citations import existence, metadata, retraction
from citation_needed.citations.extract import is_abstention
from citation_needed.citations.parse import parse_citation

STATUSES = ("abstained", "could_not_check", "fabricated", "retracted", "distorted",
            "matches_record", "supported", "unsupported")
# What counts against the model in a fabrication rate.
PROBLEM_STATUSES = frozenset({"fabricated", "retracted", "distorted", "unsupported"})


def _confidence(status: str, rec: dict) -> str:
    ex = rec.get("existence") or {}
    if status == "fabricated":
        # A DOI/PMID that no index can resolve, plus no title match, is strong evidence.
        # Title search alone can miss a work outside the indices searched.
        return "high" if (rec["parsed"].get("doi") or rec["parsed"].get("pmid")) else "medium"
    if status == "retracted":
        return "high" if len(rec["retraction"]["sources_fired"]) >= 2 else "medium"
    if status == "distorted":
        title = (rec["metadata"]["fields"] or {}).get("title")
        return "high" if title and title["similarity"] < 60 else "medium"
    if status == "matches_record":
        return "high" if ex.get("id_resolved") and rec["metadata"].get("title_sim_used", 0) >= 95 \
            else "medium"
    if status in ("supported", "unsupported"):
        return "experimental"
    return "n/a"


def _finish(rec: dict, status: str, reason: str) -> dict:
    rec["status"] = status
    rec["reason"] = reason
    rec["confidence"] = _confidence(status, rec)
    return rec


def classify_citation(citation, claim: str | None = None, use_judge: bool = False,
                      judge_model: str | None = None) -> dict:
    rec: dict = {"input": citation, "status": None, "reason": None, "confidence": None,
                 "parsed": None, "existence": None, "canonical": None,
                 "retraction": None, "metadata": None, "support": None,
                 "sources_searched": list(config.existence_sources()), "lookup_failures": []}

    if is_abstention(citation):
        return _finish(rec, "abstained", "Empty or placeholder slot: the model declined to cite.")

    parsed = parse_citation(citation)
    rec["parsed"] = parsed.to_dict()
    if not (parsed.title or parsed.doi or parsed.pmid):
        return _finish(rec, "could_not_check",
                       "No title, DOI or PMID could be read from this citation.")

    with cache.track_failures() as failures:
        exist = existence.check_existence(parsed)
    rec["existence"] = {k: v for k, v in exist.items() if k != "canonical"}
    rec["lookup_failures"] = failures
    if not exist["resolved"]:
        if failures:
            return _finish(rec, "could_not_check",
                           f"{len(failures)} lookup(s) failed, so absence is not evidence. "
                           "Rerun when the indices are reachable.")
        searched = ", ".join(rec["sources_searched"])
        return _finish(rec, "fabricated",
                       f"No record matches this citation's identifiers or title in: {searched}.")

    canonical = exist["canonical"]
    rec["canonical"] = {k: canonical.get(k) for k in
                        ("source", "title", "authors", "year", "venue", "doi", "pmid", "pmcid")}

    retr = retraction.check_retraction(canonical)
    rec["retraction"] = retr
    if retr["retracted"]:
        return _finish(rec, "retracted",
                       f"Retraction signal from: {', '.join(retr['sources_fired'])}.")

    meta = metadata.grade_metadata(parsed, canonical)
    rec["metadata"] = meta
    if meta["overall"] == "major":
        bad = [f for f, v in meta["fields"].items() if v["grade"] == "major"]
        if "title" in bad and exist.get("id_resolved") and not exist.get("title_matched"):
            why = ("The identifier resolves to a different real paper "
                   f"(title similarity {meta['fields']['title']['similarity']:.0f}/100).")
        else:
            why = f"Materially wrong: {', '.join(bad)}."
        return _finish(rec, "distorted", why)

    if use_judge and claim:
        from citation_needed.citations import support  # optional dependency
        sup = support.check_support(claim, canonical, model=judge_model)
        rec["support"] = sup
        if sup["support_label"] == "supports":
            return _finish(rec, "supported", "EXPERIMENTAL judge: the source supports the claim.")
        if sup["support_label"] in ("unrelated", "contradicts"):
            return _finish(rec, "unsupported",
                           f"EXPERIMENTAL judge: the source is {sup['support_label']} "
                           "to the claim.")

    return _finish(rec, "matches_record",
                   "Real, not retracted, metadata matches. Support for the claim not checked.")


def summarize(records: list[dict]) -> dict:
    """Counts plus a fabrication rate whose denominator excludes abstentions.

    `problem_rate` is over checked slots only: abstained and could_not_check slots are
    neither a pass nor a fail, so they are reported but kept out of the denominator.
    """
    counts = {s: 0 for s in STATUSES}
    for r in records:
        counts[r["status"]] += 1
    checked = sum(n for s, n in counts.items() if s not in ("abstained", "could_not_check"))
    problems = sum(counts[s] for s in PROBLEM_STATUSES)
    return {
        "slots": len(records),
        "checked": checked,
        "counts": counts,
        "fabricated_rate": round(counts["fabricated"] / checked, 4) if checked else None,
        "problem_rate": round(problems / checked, 4) if checked else None,
    }
