"""Find the numbers in a piece of text and normalize them so they can be compared.

"43,508", "43 508" (thin space) and "43508" are the same number. "7.82%" and
"7.82 percent" are the same quantity. "1.5 million" is 1,500,000 with a precision of
100,000. "8B to 120B" is a range of two numbers. "about 20%" is a hedged number,
"more than 40,000" is a bound. "COVID-19", "qwen3.6", "Figure 2" and "[3]" are not
numbers anyone needs to source, and are skipped.

The same extractor reads the answer and the source documents, so whatever
normalization applies to one applies to the other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from citation_needed.citations.extract import _ANY_HEADING, _REF_HEADING
from citation_needed.numbers import units as U

_SEP = "   "
_NUM_RE = re.compile(
    rf"\d{{1,3}}(?:[,{_SEP}]\d{{3}})+(?:\.\d+)?"  # 43,508 / 1,000,000.5 / 43 508
    r"|\d+(?:\.\d+)?"                              # 7.82 / 2013
    r"|(?<![\d.])\.\d+"                            # .001 (as in "P < .001")
)
# Strings that are compared as written, never as quantities.
_LITERAL_RE = re.compile(
    r"(?<![\w/:.-])(?:"
    r"\d{4}-\d{2}-\d{2}"                 # ISO date
    r"|\d{1,2}/\d{1,2}/\d{2,4}"          # 10/9/2026
    r"|\d{1,2}:\d{2}(?::\d{2})?"         # 10:30, also 1:1 ratios
    r"|\d+/\d+"                          # 3/4, 1/5
    r")(?![\w/:])"
)

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_WORD_VALUE = {w: i for i, w in enumerate(_ONES)} | {w: 20 + 10 * i for i, w in enumerate(_TENS)}
_WORD_RE = re.compile(
    rf"\b(?:(?P<tens>{'|'.join(_TENS)})(?:[- ](?P<unit>{'|'.join(_ONES[1:10])}))?"
    rf"|(?P<ones>{'|'.join(_ONES)}))"
    r"(?:[ -](?P<hundred>hundred))?\b",
    re.IGNORECASE,
)

_APPROX = re.compile(
    r"(?:\babout|\baround|\bapproximately|\bapprox\.|\broughly|\bnearly|\balmost|\bcirca|\bca\."
    r"|\bclose to|\ban estimated|\bestimated|\bsome|~|≈|∼)\s*$",
    re.IGNORECASE,
)
_GTE = re.compile(
    r"(?:\bmore than|\bgreater than|\bover|\babove|\bat least|\bexceeding|\bin excess of"
    r"|\bupwards of|>=|≥|>)\s*$",
    re.IGNORECASE,
)
_LTE = re.compile(
    r"(?:\bless than|\bfewer than|\bunder|\bbelow|\bat most|\bup to|\bno more than|<=|≤|<)\s*$",
    re.IGNORECASE,
)
# "Figure 2", "Table 1", "step 3", "No. 5": labels, not quantities.
_LABEL = re.compile(
    r"(?:\b(?:figures?|fig\.|tables?|tab\.|sections?|sec\.|chapters?|ch\.|steps?|appendix|"
    r"eq\.|equations?|items?|questions?|parts?|pages?|p\.|pp\.|versions?|lines?|rows?|columns?|"
    r"slides?|footnotes?|refs?\.?|references?|no\.|number)|#|§)\s*$",
    re.IGNORECASE,
)
_RANGE_JOIN = re.compile(r"\s*(?:-|–|—|to|through)\s*", re.IGNORECASE)
_BETWEEN = re.compile(r"\b(?:between|from)\s*$", re.IGNORECASE)
_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}
_SCALE_SUFFIX_RE = re.compile(r"(bn|mn|[kKMBb])(?![A-Za-z0-9])")
_SCALE_WORD_RE = re.compile(rf"[ {_SEP}-]+(thousand|million|billion|trillion)\b", re.IGNORECASE)

# What gets masked out of an answer before its numbers are checked.
_MASKS = [
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"\]\([^)\s]*\)"),                                   # markdown link targets
    re.compile(r"https?://\S+|www\.\S+"),
    re.compile(r"\b(?:doi:\s*)?10\.\d{4,9}/\S+", re.IGNORECASE),
    re.compile(r"\bPMID:?\s*\d+|\bPMC\d+|\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?", re.IGNORECASE),
    re.compile(r"\[\s*\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*\s*\]"),       # [1], [2, 3], [4-6]
    re.compile(r"\[\^[^\]]+\]"),                                    # footnote markers
    re.compile(r"(?m)^\s*(?:#{1,6}\s*)?(?:\d{1,3}[.)]|\(\d{1,3}\))(?=\s)"),  # list numbering
    # author-year citations: "Tang et al., 2013", "Smith and Jones (2019)", "(Eurich, 2013)"
    re.compile(r"\b[A-Z][\w'\-]+\s+(?:et al\.?|(?:and|&)\s+[A-Z][\w'\-]+),?\s*\(?(?:19|20)\d{2}[a-z]?\)?"),
    re.compile(r"\([A-Z][\w'\-]+,\s*(?:19|20)\d{2}[a-z]?(?:;\s*[A-Z][\w'\-]+,\s*(?:19|20)\d{2}[a-z]?)*\)"),
]


@dataclass
class Mention:
    start: int
    end: int
    text: str
    value: Decimal | None            # scaled, unsigned; None for literals
    precision: Decimal | None        # one unit in the last written place
    unit: str | None = None
    qualifier: str | None = None     # "approx", "gte", "lte"
    kind: str = "number"             # "number", "range", "literal"
    is_year: bool = False
    endpoints: list[Mention] = field(default_factory=list)
    scale: Decimal = Decimal(1)
    has_scale: bool = False


def mask_answer(text: str) -> str:
    """Blank out parts of an answer whose digits are not claims: reference lists,
    citation markers, identifiers, URLs, code, list numbering. Offsets are preserved."""
    chars = list(text)

    def blank(a: int, b: int) -> None:
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = " "

    pos, in_refs = 0, False
    for line in text.splitlines(keepends=True):
        if _REF_HEADING.match(line):
            in_refs = True
        elif in_refs and _ANY_HEADING.match(line):
            in_refs = False
        if in_refs:
            blank(pos, pos + len(line))
        pos += len(line)
    for rx in _MASKS:
        for m in rx.finditer(text):
            blank(m.start(), m.end())
    return "".join(chars)


_NUMBER_CHUNK = re.compile(r"[\d.,]+(?:[A-Za-z]{1,3}|%)?")


def _is_glued(text: str, s: int) -> bool:
    """True when the digits at `s` are part of a name or identifier (COVID-19, H2O,
    qwen3.6-27b). "8B-120B" and "10%-20%" are ranges, not identifiers."""
    if s == 0:
        return False
    prev = text[s - 1]
    if prev.isalnum() or prev in "_µμ":
        return True
    if prev in ".,/" and s >= 2 and text[s - 2].isdigit():
        return True
    if prev in "-–" and s >= 2 and (text[s - 2].isalnum() or text[s - 2] == "%"):
        j = s - 1
        while j > 0 and (text[j - 1].isalnum() or text[j - 1] in "_.,%"):
            j -= 1
        chunk = text[j:s - 1]
        return bool(re.search(r"[A-Za-z_]", chunk)) and not _NUMBER_CHUNK.fullmatch(chunk)
    return False


def _qualifier(before: str) -> str | None:
    if _APPROX.search(before):
        return "approx"
    if _GTE.search(before):
        return "gte"
    if _LTE.search(before):
        return "lte"
    return None


def _candidates(text: str, words: bool):
    """Yield (start, end, value, decimals) for every numeric or number-word token."""
    for m in _NUM_RE.finditer(text):
        raw = m.group(0)
        clean = raw.replace(",", "").translate({ord(c): None for c in _SEP})
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        yield m.start(), m.end(), Decimal(clean), decimals, bool(re.search(rf"[,{_SEP}]", raw))
    if words:
        for m in _WORD_RE.finditer(text):
            if m.group("tens"):
                v = _WORD_VALUE[m.group("tens").lower()]
                if m.group("unit"):
                    v += _WORD_VALUE[m.group("unit").lower()]
            else:
                v = _WORD_VALUE[m.group("ones").lower()]
            if m.group("hundred"):
                v *= 100
            yield m.start(), m.end(), Decimal(v), 0, False


def extract_numbers(text: str, *, answer: bool = True, words: bool | None = None) -> list[Mention]:
    """Every number in `text`, ranges merged, in order.

    answer=True masks citation scaffolding and skips labels ("Table 2"); use it for
    the text being checked. answer=False reads everything; use it for sources.
    Number words ("three trials") are read from sources by default and from answers
    only when asked, because "one of the..." and "two reasons" are mostly prose.
    """
    if words is None:
        words = not answer
    work = mask_answer(text) if answer else text

    literals: list[Mention] = []
    taken: list[tuple[int, int]] = []
    for m in _LITERAL_RE.finditer(work):
        literals.append(Mention(m.start(), m.end(), m.group(0), None, None, kind="literal"))
        taken.append((m.start(), m.end()))

    out: list[Mention] = []
    for s, e, base, decimals, separated in _candidates(work, words):
        if any(a <= s < b for a, b in taken) or _is_glued(work, s):
            continue
        before = work[max(0, s - 40):s]
        if answer and _LABEL.search(before):
            continue
        pos, scale, has_scale, unit = e, Decimal(1), False, None
        m = _SCALE_SUFFIX_RE.match(work, pos) if work[s:e][-1:].isdigit() else None
        if m:
            scale, has_scale, pos = U.SCALE_SUFFIX[m.group(1)], True, m.end()
        else:
            m = _SCALE_WORD_RE.match(work, pos)
            if m:
                scale, has_scale, pos = U.SCALE_WORDS[m.group(1).lower()], True, m.end()
        um = U.UNIT_RE.match(work, pos)
        if um:
            canon = U.canonical_unit(um.group("unit"))
            spaced = um.group(0)[:1] in f" -{_SEP}"
            if canon and not (canon == "fold" and um.group("unit") in "x×" and spaced):
                unit, pos = canon, um.end()
        nxt = work[pos:pos + 2]
        if nxt[:1].isalnum() or (nxt[:1] in ".," and nxt[1:2].isdigit()):
            continue  # "3D", "4th", "2nd", "3.6.1"
        cur_prefix = work[s - 1] if s and work[s - 1] in _CURRENCY else ""
        if cur_prefix and not unit:
            unit = _CURRENCY[cur_prefix]
        qual_before = work[max(0, s - 40 - len(cur_prefix)):s - len(cur_prefix)]
        value = base * scale
        out.append(Mention(
            start=s - len(cur_prefix), end=pos, text=work[s - len(cur_prefix):pos].strip(),
            value=value, precision=(Decimal(10) ** -decimals) * scale, unit=unit,
            qualifier=_qualifier(qual_before),
            is_year=(not unit and not has_scale and decimals == 0 and not separated
                     and 1900 <= base <= 2100 and len(work[s:e]) == 4),
            scale=scale, has_scale=has_scale,
        ))
    out.sort(key=lambda x: x.start)
    return sorted(_merge_ranges(work, out) + literals, key=lambda x: x.start)


def _merge_ranges(text: str, ms: list[Mention]) -> list[Mention]:
    merged: list[Mention] = []
    i = 0
    while i < len(ms):
        a = ms[i]
        if i + 1 < len(ms):
            b = ms[i + 1]
            gap = text[a.end:b.start]
            between = _BETWEEN.search(text[max(0, a.start - 12):a.start])
            joined = _RANGE_JOIN.fullmatch(gap) or (between and re.fullmatch(r"\s*and\s*", gap))
            if joined and not (a.is_year != b.is_year) and (a.unit in (None, b.unit)):
                if a.unit is None and b.unit:
                    a.unit = b.unit
                if not a.has_scale and b.has_scale:   # "1-2 million"
                    a.value *= b.scale
                    a.precision *= b.scale
                merged.append(Mention(
                    start=a.start, end=b.end, text=text[a.start:b.end].strip(), value=None,
                    precision=None, unit=b.unit, qualifier=a.qualifier, kind="range",
                    endpoints=[a, b],
                ))
                i += 2
                continue
        merged.append(a)
        i += 1
    return merged
