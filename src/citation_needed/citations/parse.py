# Ported from citation-integrity-openweight-llms (MIT, Deniz Turk 2026),
# code/harness/parse.py. Logic unchanged unless a comment says otherwise.
"""Step 1: parse an emitted citation into structured fields.

A model may emit a citation as free text ("Smith J, et al. Title of paper. Journal.
2019;12(3):45-67. doi:10.xxxx/yyyy. PMID: 12345678") or as a JSON object with explicit
fields. Both are supported. The parser is deliberately conservative: it extracts DOI,
PMID, year, and a best-effort title/author split, and records what it could not parse
so the existence check can fall back to whatever identifiers exist.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from citation_needed.citations.textutil import (
    clean_doi,
    extract_doi,
    extract_pmid,
    extract_year,
    surname_set,
)


@dataclass
class ParsedCitation:
    raw: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)      # surnames (lowercased)
    author_strings: list[str] = field(default_factory=list)  # as-emitted
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    pmid: str | None = None
    parse_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# An initial is a capital NOT followed by a lowercase letter. The study's original
# pattern lacked the lookahead, so "Majumdar SR. Metformin ..." read "SR. M" as three
# initials and the title came out as "etformin ...". Fixed here 2026-10-09.
_INITIAL = r"[A-Z](?![a-z])\.?\s*"
_AUTHOR_LEAD = re.compile(
    r"^\s*((?:[A-Z][A-Za-z'`-]+,?\s+(?:" + _INITIAL + r"){1,3}[,;&]?\s*|"
    r"(?:" + _INITIAL + r"){1,3}[A-Z][A-Za-z'`-]+[,;&]?\s*|et al\.?,?\s*){1,})"
)
_DOI_LABEL = re.compile(r"doi[:\s]*", re.IGNORECASE)


def _split_authors(chunk: str) -> list[str]:
    chunk = re.sub(r"\bet al\.?", "", chunk, flags=re.IGNORECASE)
    parts = re.split(r"[;&]|,\s*(?=[A-Z][a-z])", chunk)
    return [p.strip(" .,") for p in parts if p.strip(" .,")]


def parse_citation(citation) -> ParsedCitation:
    """Accept a dict (already structured) or a free-text string."""
    if isinstance(citation, dict):
        raw = citation.get("raw") or citation.get("citation") or str(citation)
        authors = citation.get("authors") or []
        if isinstance(authors, str):
            authors = _split_authors(authors)
        pc = ParsedCitation(
            raw=raw,
            title=citation.get("title"),
            author_strings=list(authors),
            authors=sorted(surname_set(authors)),
            year=citation.get("year"),
            venue=citation.get("venue") or citation.get("journal"),
            doi=clean_doi(citation.get("doi")),
            pmid=str(citation["pmid"]).strip() if citation.get("pmid") else None,
        )
        # fill identifiers from any embedded text if not given explicitly
        if not pc.doi:
            pc.doi = extract_doi(raw)
        if not pc.pmid:
            pc.pmid = extract_pmid(raw)
        if not pc.year:
            pc.year = extract_year(raw)
        return pc

    text = str(citation or "").strip()
    pc = ParsedCitation(raw=text)
    pc.doi = extract_doi(text)
    pc.pmid = extract_pmid(text)
    pc.year = extract_year(text)

    # Remove DOI/PMID tails before title/author heuristics
    work = text
    work = re.split(_DOI_LABEL, work)[0]
    work = re.sub(r"PMID[:\s]*\d+", "", work, flags=re.IGNORECASE)

    # Leading author block
    m = _AUTHOR_LEAD.match(work)
    if m:
        author_chunk = m.group(1)
        pc.author_strings = _split_authors(author_chunk)
        pc.authors = sorted(surname_set(pc.author_strings))
        work = work[m.end():]
    else:
        pc.parse_notes.append("no-leading-author-block")

    # Title = the first sentence-like segment after authors
    segs = [s.strip() for s in re.split(r"(?<=[.?!])\s+|\s\|\s|\.\s", work) if s.strip()]
    if segs:
        # skip a segment that is just a year/volume string
        for seg in segs:
            if len(seg) > 12 and not re.fullmatch(r"[\d\s;:()\-.,]+", seg):
                pc.title = seg.strip(" .")
                break
        # venue guess: the segment right after the title, if short and title-cased
        if pc.title and pc.title in segs:
            idx = segs.index(pc.title)
            if idx + 1 < len(segs):
                cand = segs[idx + 1]
                if 3 <= len(cand) <= 80:
                    pc.venue = cand.strip(" .")
    if not pc.title:
        pc.parse_notes.append("no-title-extracted")
    return pc
