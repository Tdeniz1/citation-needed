# Ported from citation-integrity-openweight-llms (MIT, Deniz Turk 2026),
# code/harness/textutil.py. Logic unchanged unless a comment says otherwise.
"""Text normalization + fuzzy-matching helpers shared across harness modules."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_WS = re.compile(r"\s+")
_NONALNUM = re.compile(r"[^a-z0-9 ]+")
# DOIs legitimately contain parentheses (e.g. Elsevier/Lancet 10.1016/S0140-6736(98)07037-8),
# so parens are allowed in the body and a trailing *unbalanced* ")" is trimmed afterward by
# _trim_doi (it is a citation wrapper like "(doi:10.x/y)", not part of the DOI).
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>}\]]+", re.IGNORECASE)


def _trim_doi(d: str) -> str:
    """Strip trailing citation punctuation and unbalanced close-parens from a DOI."""
    d = d.rstrip(".,;:")
    while d.endswith(")") and d.count(")") > d.count("("):
        d = d[:-1].rstrip(".,;:")
    return d
_PMID_RE = re.compile(r"\bPMID[:\s]*?(\d{4,9})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def norm_title(s: str | None) -> str:
    """Lowercase, strip accents/punctuation/extra whitespace, drop a RETRACTED prefix."""
    if not s:
        return ""
    s = strip_accents(str(s)).lower()
    s = re.sub(r"^\s*retracted[:\s]+", "", s)
    s = _NONALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def title_similarity(a: str | None, b: str | None) -> float:
    """0-100 normalized token-set similarity (order-insensitive)."""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    return float(fuzz.token_set_ratio(na, nb))


def clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = str(doi).strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = doi.strip().rstrip(".").lower()
    m = _DOI_RE.search(doi)
    return _trim_doi(m.group(0).lower()) if m else (_trim_doi(doi) if doi.startswith("10.") else None)


def _is_initials(tok: str) -> bool:
    """True for an initials block like 'JC', 'A.J.', 'SM', or a single letter."""
    t = tok.replace(".", "").strip()
    if not t:
        return True
    if len(t) == 1:
        return True
    return len(t) <= 4 and t.isupper()  # all-caps short block; 'Ng'/'Wu' are Title-case


def surname(name: str) -> str:
    """Best-effort last name from 'First Last', 'Last, First', 'F. Last', or the
    PubMed 'Last Initials' form ('LaRosa JC' -> 'larosa')."""
    if not name:
        return ""
    name = strip_accents(name).strip()
    # Drop a trailing "et al" so "Bajaj et al." -> "Bajaj" (models emit this constantly;
    # without this the last token becomes the fragment "al", zeroing author overlap and
    # producing false author-distortion / missed matches).
    name = re.sub(r"[,;]?\s*\bet\s*al\.?\b\.?", "", name, flags=re.IGNORECASE).strip(" ,;.")
    if "," in name:
        cand = name.split(",")[0]
        toks = [t for t in cand.split() if not _is_initials(t)] or cand.split()
        last = toks[-1] if toks else ""
    else:
        parts = name.split()
        non_initial = [t for t in parts if not _is_initials(t)]
        last = non_initial[-1] if non_initial else (parts[-1] if parts else "")
    last = re.sub(r"[^a-zA-Z-]", "", last).lower()
    return last


def surname_set(names) -> set[str]:
    out = set()
    for n in names or []:
        s = surname(n)
        if len(s) >= 2:
            out.add(s)
    return out


def author_overlap(parsed_surnames, canonical_surnames) -> int:
    a = set(parsed_surnames) if not isinstance(parsed_surnames, set) else parsed_surnames
    b = set(canonical_surnames) if not isinstance(canonical_surnames, set) else canonical_surnames
    return len(a & b)


def extract_doi(text: str) -> str | None:
    m = _DOI_RE.search(text or "")
    return _trim_doi(m.group(0).lower()) if m else None


def extract_pmid(text: str) -> str | None:
    m = _PMID_RE.search(text or "")
    return m.group(1) if m else None


def extract_year(text: str) -> int | None:
    m = _YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None
