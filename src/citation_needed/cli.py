"""citation-needed command line."""
from __future__ import annotations

import argparse
import os
import sys

from citation_needed import __version__
from citation_needed.report import check_answer, to_json, to_markdown

GATE_STATUSES = ("fabricated", "retracted", "distorted", "unsupported", "could_not_check")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="citation-needed",
        description="Check whether the citations in an LLM answer are real, unretracted, "
                    "and the paper they claim to be.",
    )
    ap.add_argument("answer", help="file with the LLM answer (Markdown, text or JSON); - for stdin")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--verbose", action="store_true",
                    help="JSON only: include the full per-source trace for every citation")
    ap.add_argument("--claim", help="the claim the citations are meant to support (for --judge)")
    ap.add_argument("--judge", action="store_true",
                    help="EXPERIMENTAL: ask an LLM whether each source supports --claim "
                         "(needs ANTHROPIC_API_KEY; not validated, see README)")
    ap.add_argument("--offline", action="store_true",
                    help="use only the local cache; anything uncached is could_not_check")
    ap.add_argument("--fail-on", default="",
                    help=f"comma list of statuses that make the exit code 1, e.g. "
                         f"fabricated,distorted. Choices: {','.join(GATE_STATUSES)}")
    ap.add_argument("--version", action="version", version=f"citation-needed {__version__}")
    args = ap.parse_args(argv)

    gate = {s.strip() for s in args.fail_on.split(",") if s.strip()}
    if gate - set(GATE_STATUSES):
        ap.error(f"unknown --fail-on status: {sorted(gate - set(GATE_STATUSES))}")
    if args.judge and not args.claim:
        ap.error("--judge needs --claim")
    if args.offline:
        os.environ["CITATION_NEEDED_OFFLINE"] = "1"

    text = sys.stdin.read() if args.answer == "-" else open(args.answer, encoding="utf-8").read()
    report = check_answer(text, claim=args.claim, use_judge=args.judge)
    out = to_json(report, verbose=args.verbose) if args.format == "json" else to_markdown(report)
    sys.stdout.write(out)

    hit = [r for r in report["citations"] if r["status"] in gate]
    return 1 if hit else 0


if __name__ == "__main__":
    raise SystemExit(main())
