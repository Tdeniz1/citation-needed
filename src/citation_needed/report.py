"""Run the checks over a whole answer and render the result for people or machines."""
from __future__ import annotations

import json

from citation_needed import __version__, config
from citation_needed.citations.classify import classify_citation, summarize
from citation_needed.citations.extract import extract_citations

_ICON = {
    "abstained": "○", "could_not_check": "?", "fabricated": "✗", "retracted": "✗",
    "distorted": "!", "matches_record": "✓", "supported": "✓", "unsupported": "!",
}


def check_answer(text: str, claim: str | None = None, use_judge: bool = False,
                 judge_model: str | None = None) -> dict:
    slots = extract_citations(text)
    records = [classify_citation(c, claim=claim, use_judge=use_judge, judge_model=judge_model)
               for c in slots]
    return {
        "tool": f"citation-needed {__version__}",
        "sources_searched": list(config.existence_sources()),
        "judge": "experimental" if use_judge else "off",
        "summary": summarize(records),
        "citations": records,
    }


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
    if not verbose:
        out["citations"] = [_slim(r) for r in report["citations"]]
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def _short(s, n: int = 90) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def to_markdown(report: dict) -> str:
    s = report["summary"]
    c = s["counts"]
    lines = [
        "# citation-needed report",
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
        lines.append(f"## {i}. {icon} `{rec['status']}` ({rec['confidence']}){tag}")
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
    return "\n".join(lines).rstrip() + "\n"
