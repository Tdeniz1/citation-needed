"""Find the citations in an LLM answer, and recognize when a slot is an abstention.

Input can be the JSON a structured-output prompt returns (a list of citation objects,
or {"citations": [...]}) or free text / Markdown with a reference list.

Abstention matters more than it looks. Asked for "exactly five citations", models that
cannot find five pad the list with empty objects or placeholders and often say so in
prose. The original study first scored those slots as fabricated; correcting that
reversed one model's result and removed a headline finding (CHANGELOG, 2026-08-26).
Declining to cite is the behavior we want, so an empty or placeholder slot is
reported as `abstained` and never counted against the model.
"""
from __future__ import annotations

import json
import re

from citation_needed.citations.textutil import extract_doi, extract_pmid, extract_year

# A whole slot that says "nothing here". Matched against the full slot, never a
# substring, so a real title containing "none" is not swallowed.
_PLACEHOLDER = re.compile(
    r"^(?:"
    r"n/?a|none|null|nil|tbd|unknown|placeholder|empty|-+|\.+|…|"
    r"no (?:relevant |suitable |matching |additional |further )?(?:citations?|references?|sources?)"
    r"(?: (?:available|found|provided|identified))?|"
    r"(?:citation|reference|source) (?:not )?(?:available|found|provided)|"
    r"not (?:available|applicable|found)|"
    r"(?:i )?(?:could|can) ?not find (?:a |any )?(?:relevant )?(?:citations?|references?|sources?)"
    r")$",
    re.IGNORECASE,
)

_REF_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*|\*\*)?\s*(references|bibliography|sources|citations|works cited|"
    r"literature cited|further reading)\s*:?\s*(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)
_ANY_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+•]|\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.)])\s+")


def _is_placeholder(text: str) -> bool:
    core = text.strip().strip(" .[]()*_").strip()
    return not core or bool(_PLACEHOLDER.match(core))


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, tuple)):
        return not any(str(x).strip() for x in v)
    return not str(v).strip()


def is_abstention(citation) -> bool:
    """True for a slot that carries no citation: empty object, empty string, placeholder."""
    if citation is None:
        return True
    if isinstance(citation, dict):
        identifying = ("title", "doi", "pmid", "authors", "raw", "citation")
        if all(_blank(citation.get(k)) for k in identifying):
            return True
        # {"title": "N/A", "doi": ""} is the same abstention in a different costume
        values = [str(citation.get(k)).strip() for k in identifying if not _blank(citation.get(k))]
        return all(_is_placeholder(v) for v in values)
    return _is_placeholder(str(citation))


def _try_json(text: str):
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.DOTALL)
    if fence:
        t = fence.group(1)
    if not t or t[0] not in "[{":
        return None
    try:
        data = json.loads(t)
    except ValueError:
        return None
    if isinstance(data, dict):
        for key in ("citations", "references", "sources"):
            if isinstance(data.get(key), list):
                return data[key]
        return None
    return data if isinstance(data, list) else None


def _looks_like_citation(line: str) -> bool:
    """Outside a reference section, only take lines that carry a checkable handle."""
    if extract_doi(line) or extract_pmid(line):
        return True
    # "Surname X, ... Title. Journal. 2019;12:34-56." style: a year plus 2+ sentence breaks
    return bool(extract_year(line)) and line.count(". ") >= 2 and len(line) >= 40


def extract_citations(text: str) -> list:
    """Return the citation slots in an answer, in order. Abstentions are kept as slots."""
    data = _try_json(text)
    if data is not None:
        return list(data)

    lines = text.splitlines()
    in_refs = False
    ref_items: list[str] = []
    loose: list[str] = []
    for line in lines:
        if _REF_HEADING.match(line):
            in_refs = True
            continue
        if in_refs and _ANY_HEADING.match(line):
            in_refs = False
        stripped = line.strip()
        if not stripped:
            continue
        item = _LIST_MARKER.sub("", stripped).strip()
        if in_refs:
            starts_new = (_LIST_MARKER.match(stripped) or not ref_items
                          # unmarked one-per-line references: each carries its own year
                          or (extract_year(item) and extract_year(ref_items[-1])))
            if starts_new:
                ref_items.append(item)
            else:  # wrapped continuation of the previous reference
                ref_items[-1] = f"{ref_items[-1]} {item}"
        elif _looks_like_citation(item):
            loose.append(item)
    return ref_items if ref_items else loose
