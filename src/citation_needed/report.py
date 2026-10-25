"""Run the checks over a whole answer and render the result for people or machines."""
from __future__ import annotations

import json

from citation_needed import __version__, config
from citation_needed.citations.classify import classify_citation, summarize
from citation_needed.citations.extract import extract_citations
from citation_needed.numbers import check_numbers
from citation_needed.numbers.sources import SourceDoc

_ICON = {
    "abstained": "○", "could_not_check": "?", "fabricated": "✗", "retracted": "✗",
    "distorted": "!", "matches_record": "✓", "supported": "✓", "unsupported": "!",
}


def check_answer(text: str, claim: str | None = None, use_judge: bool = False,
                 judge_model: str | None = None, *, citations: bool = True,
                 sources: list[SourceDoc] | None = None, skipped_sources: list[str] | None = None,
                 ignore_years: bool = False, number_words: bool = False) -> dict:
    """Check citations (network, cached) and, when `sources` is given, numbers (local)."""
    report: dict = {"tool": f"citation-needed {__version__}"}
    if citations:
        records = [classify_citation(c, claim=claim, use_judge=use_judge, judge_model=judge_model)
                   for c in extract_citations(text)]
        report |= {
            "sources_searched": list(config.existence_sources()),
            "judge": "experimental" if use_judge else "off",
            "summary": summarize(records),
            "citations": records,
        }
    if sources is not None:
        report["numbers"] = check_numbers(text, sources, ignore_years=ignore_years,
                                          words=number_words, skipped_sources=skipped_sources)
    return report


def _slim(rec: dict) -> dict:
    """The fields a person or agent acts on; the full trace stays in --verbose output."""
    parsed = rec.get("parsed") or {}
    canonical = rec.get("canonical") or {}
    return {
        "status": rec["status"],
        "confidence": rec["confidence"],
        "reason": rec["reason"],
        "claimed": {k: parsed.get(k) for k in ("title", "year", "doi", "pmid")} if parsed else None,
        "resolved": {k: canonical.get(k) for k in ("title", "year", "venue", "doi", "pmid")}
        if canonical else None,
        "input": rec["input"],
    }


def to_json(report: dict, verbose: bool = False) -> str:
    out = dict(report)
    if not verbose and "citations" in report:
        out["citations"] = [_slim(r) for r in report["citations"]]
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def _short(s, n: int = 90) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _citations_md(report: dict) -> list[str]:
    s = report["summary"]
    c = s["counts"]
    lines = [
        "## Citations",
        "",
        f"{s['slots']} citation slot(s): {s['checked']} checked, {c['abstained']} abstained, "
        f"{c['could_not_check']} could not be checked.",
    ]
    if s["checked"]:
        lines.append(
            f"Fabricated {c['fabricated']} · retracted {c['retracted']} · "
            f"distorted {c['distorted']} · matches a real record "
            f"{c['matches_record'] + c['supported']}"
            + (f" · unsupported {c['unsupported']} (experimental)" if c["unsupported"] else "")
            + "."
        )
    lines += ["", f"Searched: {', '.join(report['sources_searched'])}. "
              f"Support judge: {report['judge']}.", ""]

    for i, rec in enumerate(report["citations"], 1):
        icon = _ICON[rec["status"]]
        tag = " **[citation needed]**" if rec["status"] in ("fabricated", "retracted",
                                                             "distorted") else ""
        lines.append(f"### {i}. {icon} `{rec['status']}` ({rec['confidence']}){tag}")
        lines.append("")
        raw = rec["input"]
        lines.append(f"> {_short(raw if isinstance(raw, str) else json.dumps(raw), 240)}")
        lines.append("")
        lines.append(rec["reason"])
        parsed, canonical = rec.get("parsed") or {}, rec.get("canonical") or {}
        if canonical and rec["status"] in ("distorted", "matches_record", "retracted",
                                           "supported", "unsupported"):
            lines += [
                "",
                f"- Claimed: {_short(parsed.get('title'))} ({parsed.get('year') or 'no year'})",
                f"- Resolved: {_short(canonical.get('title'))} ({canonical.get('year')}, "
                f"{canonical.get('venue') or 'no venue'})",
            ]
            ident = canonical.get("doi") and f"https://doi.org/{canonical['doi']}" \
                or canonical.get("pmid") and f"https://pubmed.ncbi.nlm.nih.gov/{canonical['pmid']}/"
            if ident:
                lines.append(f"- Record: {ident}")
        lines.append("")
    return lines


_NUM_ICON = {"found": "✓", "rounded": "✓", "approximate": "~", "unit_mismatch": "!", "not_found": "✗"}


def _numbers_md(nums: dict) -> list[str]:
    s, c = nums["summary"], nums["summary"]["counts"]
    lines = [
        "## Numbers",
        "",
        f"{s['numbers']} number(s) checked against {len(nums['sources'])} source document(s): "
        f"found {c['found']} · rounded {c['rounded']} · approximate {c['approximate']} · "
        f"unit mismatch {c['unit_mismatch']} · not in any source {c['not_found']}.",
    ]
    if s["ignored_years"]:
        lines.append(f"Ignored {s['ignored_years']} year(s) (--ignore-years).")
    for sk in nums["skipped_sources"]:
        lines.append(f"Skipped source {sk}.")
    lines.append("")
    if not nums["numbers"]:
        lines.append("No numbers found in the answer.")
    # flagged numbers first, then the rest, each in answer order
    ordered = sorted(nums["numbers"], key=lambda r: (r["status"] not in ("not_found", "unit_mismatch"),
                                                   r["line"]))
    for r in ordered:
        tag = " **[number needed]**" if r["status"] in ("not_found", "unit_mismatch") else ""
        lines.append(f"- {_NUM_ICON[r['status']]} `{r['status']}` **{r['text']}** "
                     f"(line {r['line']}){tag}: {r['reason']}")
        if r["status"] in ("not_found", "unit_mismatch"):
            lines.append(f"  > {_short(r['context'], 200)}")
    lines.append("")
    return lines


def to_markdown(report: dict) -> str:
    lines = ["# citation-needed report", ""]
    if "citations" in report:
        lines += _citations_md(report)
    if "numbers" in report:
        lines += _numbers_md(report["numbers"])
    return "\n".join(lines).rstrip() + "\n"
