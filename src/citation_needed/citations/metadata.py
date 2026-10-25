# Ported from github.com/Tdeniz1/Open-Weight-LLM-Medical-Citation-Audit (MIT, Deniz Turk 2026),
# code/harness/metadata.py. Logic unchanged unless a comment says otherwise.
"""Step 4: metadata field-agreement grading.

For a resolved, non-retracted work, compare what the model emitted against the canonical
record and grade distortion as none / minor / major. Only "major" is scored Distorted by
the classifier (harness/classify.py), so this grade is what separates a materially wrong
reference from a benign formatting difference. The threshold logic below is written to
match the brief's definition of Distorted -- "resolves to a real work but the model got
authors, year, venue, or title MATERIALLY wrong" -- and, crucially, to NOT inflate that
count with near-matches to the same real work.

------------------------------------------------------------------------------------------
Why this grader was hardened (methods note; read before changing a threshold)
------------------------------------------------------------------------------------------
A field-audit of the first large run found that a naive "worst field grade" rule produced
an implausibly high distortion rate. Three benign patterns were being scored "major":

  1. Wrong-identifier / arbitrary-canonical.  The existence check resolves a citation by
     ANY identifier it can (DOI via Crossref, PMID via PubMed, ...), and when a model emits
     one good identifier and one bad one they point at two DIFFERENT real works. The old
     grader compared the emitted title against a single priority-ordered canonical, so when
     the bad identifier happened to win the priority order the emitted title was scored a
     "major" title distortion even though the model had ALSO supplied the correct paper
     (sitting in canonical["all_source_records"]). Example: a model emits the real Nature
     paper "Gut flora metabolism of phosphatidylcholine promotes cardiovascular disease"
     with its correct DOI plus a stray wrong PMID; the PMID resolved to an unrelated
     veterinary paper and drove a false "major".
     -> Fix: grade against the resolved source record whose TITLE best matches the emitted
        title (the work the citation most plausibly refers to), not an arbitrary pick.

  2. Author-list artifacts.  Exact author-surname overlap is a noisy signal. Consortium /
     organizational works (GBD, WHO, ACC/AHA, professional-society statements) frequently
     carry an EMPTY author list in Crossref, so any emitted author scored zero overlap ->
     "major". Models also legitimately truncate to first-N authors or "et al." (the brief
     notes this), "[No authors listed]" parses to the junk surname "listed", and hyphenated
     or compound surnames and PubMed-vs-Crossref formatting break exact-set overlap. In the
     audit, author-driven "major" grades were essentially all such artifacts on a paper
     whose TITLE already matched the canonical.
     -> Fix: authorship is retained as an informative signal but NEVER escalates past
        "minor". A reference that names a genuinely different/nonexistent work always also
        mismatches on TITLE, which the grader still flags as major. Empty canonical author
        lists are skipped (unjudgeable), and overlap is computed tolerantly.

  3. Unicode / punctuation title variants.  En-dash vs hyphen in year ranges
     ("1990-2015" vs "1990–2015"), an em-dash in "Diabetes—2010", a dropped
     trailing period, or a dropped subtitle are not material distortions. token_set_ratio
     already tolerates most of these; they are kept as none/minor by the same thresholds.

What still counts as "major" (genuine distortion), unchanged in spirit:
  * TITLE materially wrong: the emitted title does not match ANY resolved source record
    above the minor threshold (the dominant genuine case -- weak models emit a plausible
    title that corresponds to no real paper, attached to a resolvable-but-unrelated id).
  * YEAR wrong by more than one: a multi-year error even on the correctly identified work
    is a real metadata distortion (brief: "wrong year by years").

Deterministic grade thresholds (rapidfuzz token_set_ratio, 0-100):
  * title : >=95 none, >=80 minor, else major   (graded vs the best-matching source record)
  * year  : exact none; off-by-one minor; more major
  * author: none if the emitted lead surname is present (tolerant match), else minor;
            skipped when either side has no usable author list; capped at minor by design
  * venue : >=60 none, else minor (never major)
The overall grade is the worst field grade. Only "major" is scored Distorted downstream.

Known residual (documented, out of scope for this module): when a model supplies a correct
TITLE but every emitted identifier is wrong AND the correct paper is found only by a
title search on the SAME index that already resolved a wrong identifier, the existence
check discards that title-search hit, so the correct record is absent from
all_source_records and this grader cannot recover it -> such a citation is still scored a
title "major". Fixing it requires a canonical-selection change in harness/existence.py
(prefer the resolved/searched record whose title best matches the emission), not here.
"""
from __future__ import annotations

from citation_needed.citations.parse import ParsedCitation
from citation_needed.citations.textutil import surname, title_similarity
from citation_needed.config import VENUE_SIM_MINOR, YEAR_MINOR_TOL

_RANK = {"none": 0, "minor": 1, "major": 2}

# Title-similarity cutpoints (token_set_ratio, 0-100).
TITLE_SIM_NONE = 95.0    # near-identical title -> the same work, no title distortion
TITLE_SIM_MINOR = 80.0   # clear variant of the same title -> minor, not major

# Emitted "author" tokens that are not real surnames. These come from the parser seeing
# byline placeholders ("[No authors listed]", "N/A", "Anonymous") or trailing corporate/
# consortium words; grading them against a real author list produces spurious zero overlap.
_NON_NAME = {
    "listed", "na", "none", "author", "authors", "anonymous", "group", "committee",
    "collaborators", "collaboration", "consortium", "care", "et", "al", "team",
    "study", "network", "trial", "the", "no",
}


def _worst(grades: list[str]) -> str:
    return max(grades, key=lambda g: _RANK[g]) if grades else "none"


def _clean_surnames(names) -> set[str]:
    """Normalize an author list to a set of comparable surnames, dropping placeholders."""
    out: set[str] = set()
    for n in names or []:
        s = surname(str(n))
        if len(s) >= 2 and s not in _NON_NAME:
            out.add(s)
    return out


def _soft_overlap(emitted: set[str], canonical: set[str]) -> int:
    """Surname-set overlap tolerant of hyphenated/compound forms.

    An exact intersection is counted first; otherwise a prefix relation on sufficiently
    long tokens ('arjona' vs 'arjona-sanchez' -> 'arjonasanchez') counts as a match, since
    those denote the same author under different formatting conventions.
    """
    inter = emitted & canonical
    if inter:
        return len(inter)
    for a in emitted:
        for b in canonical:
            if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
                return 1
    return 0


def _candidate_records(canonical: dict) -> list[dict]:
    """Every real work the existence check resolved for this citation.

    existence.check_existence stashes the per-source normalized records under
    canonical["all_source_records"]; when a model supplies conflicting identifiers these
    are DIFFERENT real papers, and we must grade against the one the citation refers to.
    """
    all_src = canonical.get("all_source_records") or {}
    return list(all_src.values()) or [canonical]


def _choose_record(parsed_title: str | None, canonical: dict) -> tuple[dict, float]:
    """Pick the resolved record the citation most plausibly refers to.

    That is the source record whose title best matches the emitted title. Falls back to
    the priority-ordered canonical when no title was emitted. This neutralizes the
    arbitrary-canonical false positive: a stray wrong identifier can no longer be blamed
    on the emitted title when the model also resolved the correct work.
    """
    if not parsed_title:
        return canonical, title_similarity(parsed_title, canonical.get("title"))
    best: tuple[dict, float] | None = None
    for rec in _candidate_records(canonical):
        t = rec.get("title")
        if not t:
            continue
        sim = title_similarity(parsed_title, t)
        if best is None or sim > best[1]:
            best = (rec, sim)
    return best if best is not None else (canonical, 0.0)


def _safe_year(y) -> int | None:
    """Parse an emitted/canonical year to int, tolerating junk like '...' or 'n.d.'."""
    try:
        return int(str(y))
    except (ValueError, TypeError):
        return None


def grade_metadata(parsed: ParsedCitation, canonical: dict) -> dict:
    fields: dict[str, dict] = {}
    grades: list[str] = []

    # Grade against the resolved record the citation most plausibly refers to.
    chosen, tsim = _choose_record(parsed.title, canonical)
    primary_sim = title_similarity(parsed.title, canonical.get("title")) if parsed.title else 0.0

    # Title -- the field that distinguishes a genuinely wrong reference from a variant.
    if parsed.title and chosen.get("title"):
        g = "none" if tsim >= TITLE_SIM_NONE else ("minor" if tsim >= TITLE_SIM_MINOR else "major")
        fields["title"] = {"grade": g, "similarity": tsim,
                           "emitted": parsed.title, "canonical": chosen.get("title")}
        grades.append(g)

    # Year -- exact none, off-by-one minor, more major (a multi-year error is a real
    # distortion even when the work is otherwise correctly identified).
    py, cy = _safe_year(parsed.year), _safe_year(chosen.get("year"))
    if py and cy:
        diff = abs(py - cy)
        g = "none" if diff == 0 else ("minor" if diff <= YEAR_MINOR_TOL else "major")
        fields["year"] = {"grade": g, "emitted": py, "canonical": cy}
        grades.append(g)

    # Authors -- informative but capped at minor (see module rationale). Skipped when
    # either side lacks a usable author list (consortium/organizational works, placeholders).
    emit_auth = _clean_surnames(parsed.authors)
    can_auth = _clean_surnames(chosen.get("authors") or [])
    if emit_auth and can_auth:
        ov = _soft_overlap(emit_auth, can_auth)
        lead = surname(str(parsed.authors[0])) if parsed.authors else ""
        lead_ok = bool(lead) and (lead in can_auth or _soft_overlap({lead}, can_auth) >= 1)
        g = "none" if lead_ok else "minor"   # never "major": authorship alone is too noisy
        fields["authors"] = {"grade": g, "overlap": ov,
                             "emitted": sorted(emit_auth), "canonical": sorted(can_auth)}
        grades.append(g)

    # Venue -- title-similarity of journal names; abbreviations/formatting are at most minor.
    if parsed.venue and chosen.get("venue"):
        vsim = title_similarity(parsed.venue, chosen["venue"])
        g = "none" if vsim >= VENUE_SIM_MINOR else "minor"
        fields["venue"] = {"grade": g, "similarity": vsim,
                          "emitted": parsed.venue, "canonical": chosen["venue"]}
        grades.append(g)

    overall = _worst(grades)
    return {
        "overall": overall,
        "fields": fields,
        "n_fields_compared": len(grades),
        # audit: which resolved record we graded against, and whether it differed from the
        # priority canonical (i.e. an intent-aligned re-selection corrected a wrong pick).
        "graded_against_source": chosen.get("source"),
        "graded_against_title": chosen.get("title"),
        "title_sim_used": tsim,
        "title_sim_primary_canonical": primary_sim,
    }
