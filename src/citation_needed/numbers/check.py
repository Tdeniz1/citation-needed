"""Check that every number in an answer appears in the source documents it rests on.

Each number in the answer gets exactly one status:

  found          the same value appears in a source (after normalization: separators,
                 scale words, percent spellings, exact unit conversion).
  rounded        the answer is a rounding of a more precise source value
                 ("7.8%" from "7.82%", "1.5 million" from "1,523,000").
  approximate    the answer hedges ("about 20%", "more than 40,000") and a source value
                 is inside the hedge: within 10% for "about", on the right side and
                 within 25% for a bound.
  unit_mismatch  the value appears, but only with a different, inconvertible unit
                 ("5 mg" in the answer, "5 g" in the source). Flagged.
  not_found      no source carries this number. Flagged.

Ranges ("8B to 120B", "10-20%") are checked endpoint by endpoint and take the worst
endpoint's status. Dates, times and fractions ("2026-08-26", "1/5") must appear as
written.

What it cannot tell you: whether a number that *is* in the sources is attached to the
right thing. "Fabrication was 7.82%" passes if 7.82 appears anywhere in the sources,
even next to a different quantity. This is a provenance check, not a fact check.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from decimal import Decimal

from citation_needed.numbers import units as U
from citation_needed.numbers.extract import Mention, extract_numbers
from citation_needed.numbers.sources import SourceDoc

STATUSES = ("found", "rounded", "approximate", "unit_mismatch", "not_found")
FLAGGED = frozenset({"unit_mismatch", "not_found"})
_RANK = {s: i for i, s in enumerate(STATUSES)}

APPROX_TOLERANCE = Decimal("0.10")
BOUND_TOLERANCE = Decimal("0.25")
_QUAL_WORD = {"approx": "hedged", "gte": "lower bound", "lte": "upper bound"}


@dataclass
class _Entry:
    value: Decimal
    unit: str | None
    doc: SourceDoc
    start: int
    end: int
    text: str

    # Line and context are only computed for the few entries cited as evidence.
    @property
    def line(self) -> int:
        return _line_of(self.doc.text, self.start)

    @property
    def context(self) -> str:
        return _context(self.doc.text, self.start, self.end)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _context(text: str, start: int, end: int, width: int = 160) -> str:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le == -1 else le
    line = text[ls:le]
    a, b = start - ls, end - ls
    if len(line) > width:
        pad = max(0, (width - (b - a)) // 2)
        lo, hi = max(0, a - pad), min(len(line), b + pad)
        line = ("…" if lo else "") + line[lo:hi] + ("…" if hi < len(line) else "")
    return " ".join(line.split())


def _fmt(v: Decimal) -> str:
    return format(v.normalize(), "f")


class SourceIndex:
    """Every number in the source documents, sorted by value for windowed lookup."""

    def __init__(self, docs: list[SourceDoc]):
        self.docs = [d.name for d in docs]
        entries: list[_Entry] = []
        self.literals: dict[str, list[_Entry]] = {}
        for d in docs:
            for m in extract_numbers(d.text, answer=False):
                parts = m.endpoints if m.kind == "range" else [m]
                for p in parts:
                    e = _Entry(p.value, p.unit, d, p.start, p.end, p.text)
                    if p.kind == "literal":
                        self.literals.setdefault(p.text, []).append(e)
                    else:
                        entries.append(e)
        entries.sort(key=lambda e: e.value)
        self._entries = entries
        self._keys = [e.value for e in entries]

    def window(self, lo: Decimal, hi: Decimal) -> list[_Entry]:
        return self._entries[bisect.bisect_left(self._keys, lo):bisect.bisect_right(self._keys, hi)]


def _status_against(m: Mention, a_val: Decimal, prec: Decimal, s_val: Decimal) -> str | None:
    diff = abs(s_val - a_val)
    if diff == 0:
        return "found"
    if m.qualifier == "gte":
        ok = s_val >= a_val and diff <= BOUND_TOLERANCE * a_val
        return "approximate" if ok else None
    if m.qualifier == "lte":
        ok = s_val <= a_val and diff <= BOUND_TOLERANCE * a_val
        return "approximate" if ok else None
    if diff <= prec / 2:
        return "rounded" if m.qualifier is None else "approximate"
    if m.qualifier == "approx" and diff <= APPROX_TOLERANCE * a_val:
        return "approximate"
    return None


def _reach(m: Mention, value: Decimal, prec: Decimal) -> Decimal:
    """How far from `value` a source number can be and still match `m`."""
    if m.qualifier in ("gte", "lte"):
        return max(prec, BOUND_TOLERANCE * value)
    if m.qualifier == "approx":
        return max(prec, APPROX_TOLERANCE * value)
    return prec


def _match_number(m: Mention, idx: SourceIndex) -> tuple[str, list[_Entry]]:
    a, prec = m.value, m.precision
    best, evidence = "not_found", []

    def consider(status: str, e: _Entry) -> None:
        nonlocal best, evidence
        if _RANK[status] < _RANK[best]:
            best, evidence = status, [e]
        elif status == best and len(evidence) < 3:
            evidence.append(e)

    reach = _reach(m, a, prec)
    for e in idx.window(a - reach, a + reach):
        compatible = m.unit is None or e.unit is None or m.unit == e.unit
        if compatible:
            st = _status_against(m, a, prec, e.value)
            if st:
                consider(st, e)
        elif e.value == a:   # "5 mg" vs "5 g", "20%" vs "20 days"
            consider("unit_mismatch", e)
    # exact conversions within a dimension: 0.5 g vs 500 mg, 2 h vs 120 min
    if m.unit and U.factor(m.unit) is not None:
        for other in U.convertible_units(m.unit):
            if other == m.unit:
                continue
            ratio = U.factor(m.unit) / U.factor(other)
            target, tprec = a * ratio, prec * ratio
            treach = _reach(m, target, tprec)
            for e in idx.window(target - treach, target + treach):
                if e.unit == other:
                    st = _status_against(m, target, tprec, e.value)
                    if st:
                        consider(st, e)
    return best, evidence


def _reason(m: Mention, status: str, ev: list[_Entry]) -> str:
    if not ev:
        return "No source document contains this number."
    e = ev[0]
    where = f"{e.doc.name}:{e.line}"
    if status == "found":
        return f"Appears in {where} as \"{e.text}\"."
    if status == "rounded":
        return f"Rounded from \"{e.text}\" in {where}."
    if status == "approximate":
        return f"{_QUAL_WORD.get(m.qualifier, 'hedged').capitalize()}; \"{e.text}\" in {where} is inside it."
    return f"The value appears only as \"{e.text}\" in {where}, with a different unit."


def _record(m: Mention, idx: SourceIndex, text: str) -> dict:
    rec = {
        "text": m.text, "line": _line_of(text, m.start), "context": _context(text, m.start, m.end),
        "kind": m.kind, "value": None if m.value is None else _fmt(m.value), "unit": m.unit,
        "qualifier": m.qualifier, "status": None, "reason": None, "evidence": [],
    }
    if m.kind == "literal":
        hits = idx.literals.get(m.text, [])
        status = "found" if hits else "not_found"
        ev = hits[:3]
    elif m.kind == "range":
        rec["endpoints"] = [_record(p, idx, text) for p in m.endpoints]
        worst = max(rec["endpoints"], key=lambda r: _RANK[r["status"]])
        rec["status"], rec["reason"] = worst["status"], (
            "Both ends are in the sources." if worst["status"] not in FLAGGED
            else f"\"{worst['text']}\": {worst['reason']}")
        rec["evidence"] = [ev for r in rec["endpoints"] for ev in r["evidence"][:1]]
        return rec
    else:
        status, ev = _match_number(m, idx)
    rec["status"] = status
    rec["reason"] = _reason(m, status, ev)
    rec["evidence"] = [{"source": e.doc.name, "line": e.line, "text": e.text, "context": e.context}
                       for e in ev]
    return rec


def check_numbers(answer: str, sources: list[SourceDoc], *, ignore_years: bool = False,
                  words: bool = False, skipped_sources: list[str] | None = None) -> dict:
    """Check every number in `answer` against `sources`. Deterministic, no network."""
    idx = SourceIndex(sources)
    mentions = extract_numbers(answer, answer=True, words=words)
    ignored = 0
    records = []
    for m in mentions:
        if ignore_years and (m.is_year or (m.kind == "range" and all(p.is_year for p in m.endpoints))):
            ignored += 1
            continue
        records.append(_record(m, idx, answer))
    counts = {s: 0 for s in STATUSES}
    for r in records:
        counts[r["status"]] += 1
    return {
        "sources": idx.docs,
        "skipped_sources": list(skipped_sources or []),
        "summary": {
            "numbers": len(records),
            "counts": counts,
            "flagged": sum(counts[s] for s in FLAGGED),
            "ignored_years": ignored,
        },
        "numbers": records,
    }
