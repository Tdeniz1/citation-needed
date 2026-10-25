"""MCP server: let an agent check its own draft before it answers.

Two tools:

  check_citations  every citation in a draft gets a verdict (fabricated, retracted,
                   distorted, matches_record, abstained, could_not_check) with the
                   evidence, plus plain instructions for what to fix.
  check_numbers    every number in a draft is looked up in the source documents the
                   agent was given; numbers that appear nowhere are flagged.

Run it with `citation-needed-mcp` (stdio). Needs the optional extra:
`pip install "citation-needed[mcp]"`. Works with the official MCP Python SDK 1.x
(FastMCP) and 2.x (MCPServer).
"""
from __future__ import annotations

import json
import sys

from citation_needed import __version__
from citation_needed.numbers import SourceDoc, load_sources
from citation_needed.report import check_answer, to_json

INSTRUCTIONS = (
    "Use these tools on your own draft before you send it. check_citations looks every "
    "reference up in Crossref and PubMed: remove or correct anything fabricated, retracted "
    "or distorted, and never replace a bad citation with one you have not seen. Saying "
    "that you could not find a source is a good outcome. check_numbers needs the source "
    "documents you were given: any number flagged not_found should be removed or traced "
    "to a source. A pass from either tool means the reference or number exists, not that "
    "it supports your claim."
)

_FIX = {
    "fabricated": "No index has this work. Remove it; do not invent a replacement.",
    "retracted": "This work has been retracted. Remove it, or say explicitly that it was retracted.",
    "distorted": "The identifier points to a different paper, or the metadata is materially wrong. "
                 "Correct it from the resolved record shown, or remove it.",
    "could_not_check": "The lookup failed. Do not treat this as verified.",
}


def check_citations(text: str) -> dict:
    """Check every citation in `text` (Markdown, plain text, or JSON citation list)."""
    report = json.loads(to_json(check_answer(text)))
    report["fix"] = [
        {"citation": i, "status": c["status"], "action": _FIX[c["status"]]}
        for i, c in enumerate(report["citations"], 1) if c["status"] in _FIX
    ]
    return report


def check_numbers(text: str, source_texts: list[str] | None = None,
                  source_paths: list[str] | None = None, ignore_years: bool = False) -> dict:
    """Check every number in `text` against the given source texts and/or files."""
    docs = [SourceDoc(f"source_texts[{i}]", t) for i, t in enumerate(source_texts or [])]
    skipped: list[str] = []
    if source_paths:
        loaded, skipped = load_sources(source_paths)
        docs += loaded
    if not docs:
        raise ValueError("check_numbers needs at least one source: pass source_texts or source_paths")
    report = check_answer(text, citations=False, sources=docs, skipped_sources=skipped,
                          ignore_years=ignore_years)
    nums = report["numbers"]
    nums["fix"] = [
        {"number": r["text"], "line": r["line"], "status": r["status"],
         "action": "Remove this number or trace it to a source; it is not in any document provided."
         if r["status"] == "not_found" else
         "The value is in the sources only with a different unit; check which one is right."}
        for r in nums["numbers"] if r["status"] in ("not_found", "unit_mismatch")
    ]
    return nums


CHECK_CITATIONS_DESCRIPTION = (
    "Verify the citations in a draft answer against Crossref and PubMed. Returns one verdict "
    "per citation: fabricated (no index has it), retracted, distorted (real identifier, "
    "different paper or wrong metadata), matches_record (real and matching; support for the "
    "claim is NOT checked), abstained (empty slot, which is fine), or could_not_check. "
    "Pass the full draft including its reference list. Free, no API key; results are cached."
)
CHECK_NUMBERS_DESCRIPTION = (
    "Check that every number in a draft answer appears in the source documents it is based "
    "on. Pass the draft as `text` and the sources as `source_texts` (strings) and/or "
    "`source_paths` (local files or folders). Each number comes back found, rounded, "
    "approximate, unit_mismatch or not_found. Handles thousands separators, percentages, "
    "scale words, ranges, units and hedges like 'about'. Local only, no network."
)


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
    server = Server("citation-needed", instructions=INSTRUCTIONS)
    server.tool(name="check_citations", description=CHECK_CITATIONS_DESCRIPTION)(check_citations)
    server.tool(name="check_numbers", description=CHECK_NUMBERS_DESCRIPTION)(check_numbers)
    return server


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("--version", "-V"):
        print(f"citation-needed-mcp {__version__}")
        return 0
    if args and args[0] in ("--help", "-h"):
        print(__doc__)
        return 0
    try:
        server = build_server()
    except ImportError:
        print('citation-needed-mcp needs the MCP SDK: pip install "citation-needed[mcp]"',
              file=sys.stderr)
        return 1
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
