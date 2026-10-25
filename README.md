# citation-needed

[![ci](https://github.com/Tdeniz1/citation-needed/actions/workflows/ci.yml/badge.svg)](https://github.com/Tdeniz1/citation-needed/actions/workflows/ci.yml)
[![study DOI](https://img.shields.io/badge/study-10.5281%2Fzenodo.22030231-blue)](https://doi.org/10.5281/zenodo.22030231)
[![license: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**An LLM's reference list can look perfect and still be wrong. citation-needed checks it.**

Give it an answer an LLM wrote. For every citation it tells you whether the work exists, whether it has been retracted, and whether the DOI or PMID actually points to the paper the model described. Give it the source documents too, and it flags every number in the answer that does not appear in them. It runs as a command line tool, as a CI gate, or as an MCP server so an agent can check its own draft before it answers.

It is the checking pipeline from a study of 43,508 citations produced by six open-weight models ([Turk 2026](#citing)), turned into a tool.

## What it reports

This is a real report, pasted from a live run (Crossref and PubMed, 2026-10-25) on [`examples/answer.md`](examples/answer.md):

```markdown
# citation-needed report

## Citations

5 citation slot(s): 4 checked, 1 abstained, 0 could not be checked.
Fabricated 1 · retracted 1 · distorted 1 · matches a real record 1.

Searched: crossref, pubmed. Support judge: off.

### 1. ✓ `matches_record` (high)

> Tang WH, Wang Z, Levison BS, et al. Intestinal microbial metabolism of phosphatidylcholine and cardiovascular risk. N Engl J Med. 2013;368(17):1575-1584. doi:10.1056/NEJMoa1109400

Real, not retracted, metadata matches. Support for the claim not checked.

- Claimed: Intestinal microbial metabolism of phosphatidylcholine and cardiovascular risk (2013)
- Resolved: Intestinal microbial metabolism of phosphatidylcholine and cardiovascular risk. (2013, The New England journal of medicine)
- Record: https://doi.org/10.1056/nejmoa1109400

### 2. ✗ `retracted` (medium) **[citation needed]**

> Wakefield AJ, Murch SH, Anthony A, et al. Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children. Lancet. 1998;351(9103):637-641. PMID: 9500320

Retraction signal from: pubmed.

- Claimed: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental dis… (1998)
- Resolved: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental dis… (1998, Lancet (London, England))
- Record: https://doi.org/10.1016/s0140-6736(97)11096-0

### 3. ! `distorted` (high) **[citation needed]**

> Eurich DT, Weir DL, Majumdar SR. Metformin lowers all-cause mortality in elderly patients with chronic heart failure: a cohort study. N Engl J Med. 2013;368(17):1575-1584. doi:10.1056/NEJMoa1109400

The identifier resolves to a different real paper (title similarity 41/100).

- Claimed: Metformin lowers all-cause mortality in elderly patients with chronic heart failure: a co… (2013)
- Resolved: Intestinal Microbial Metabolism of Phosphatidylcholine and Cardiovascular Risk (2013, New England Journal of Medicine)
- Record: https://doi.org/10.1056/nejmoa1109400

### 4. ✗ `fabricated` (high) **[citation needed]**

> Johnson R, Patel K. Quantum resonance therapy reverses type 2 diabetes in adults: a randomized controlled trial. J Clin Endocrinol Metab. 2021;106(4):1123-1131. doi:10.1210/clinem/dgab99417

No record matches this citation's identifiers or title in: crossref, pubmed.

### 5. ○ `abstained` (n/a)

> No relevant citation found.

Empty or placeholder slot: the model declined to cite.
```

Citation 3 is the failure most checkers miss: its DOI is real and resolves fine, to a different paper.

And the numbers check, on [`examples/numbers-answer.md`](examples/numbers-answer.md), a summary of the study that repeats three figures the study later corrected, checked against the study's abstract in [`examples/sources/`](examples/sources/). Real output, with nine passing lines cut for length:

```markdown
15 number(s) checked against 1 source document(s): found 8 · rounded 3 · approximate 1 · unit mismatch 0 · not in any source 3.

- ✗ `not_found` **43,631** (line 1) **[number needed]**: No source document contains this number.
  > A study of open-weight models scored 43,631 citations from about 9,300 responses over
- ✗ `not_found` **2.14** (line 4) **[number needed]**: No source document contains this number.
  > check its own citations backfired: fabrication rose from 7.13% to 16.2% (OR 2.14).
- ✗ `not_found` **76.4%** (line 5) **[number needed]**: No source document contains this number.
  > Tool use cut the fabrication odds by 76.4%. The response-level bad-citation rate fell
- ~ `approximate` **9,300** (line 1): Hedged; "9,284" in examples/sources/turk2026-abstract-results.md:6 is inside it.
- ✓ `rounded` **7.13% to 16.2%** (line 4): Both ends are in the sources.
- ✓ `rounded` **29.1%** (line 6): Rounded from "29.06%" in examples/sources/turk2026-abstract-results.md:6.
```

All three superseded figures are flagged, and nothing else is.

### Live smoke run

Before this release the checker was run live on three answers: `examples/answer.md` and the two in [`examples/live/`](examples/live/), with 18 citation slots between them, chosen by hand to cover every verdict. **17 of 18 verdicts are right.** The first run got 16 right: it called two real papers `distorted` and showed a wrong DOI on a third. Those bugs are fixed, and each one now has a regression test ([`tests/test_live_regressions.py`](tests/test_live_regressions.py)). The one remaining miss is a known limitation, described [below](#what-is-not-validated). Eighteen hand-picked cases show the tool works end to end. They are not an accuracy estimate.

## Quick start

Not on PyPI yet. Install from source:

```bash
git clone https://github.com/Tdeniz1/citation-needed
cd citation-needed
pip install -e ".[mcp]"                         # [mcp] for the MCP server, [pdf] to read PDF sources

export CITATION_NEEDED_EMAIL=you@example.org    # polite-pool contact for Crossref and NCBI (recommended)

citation-needed answer.md                       # check citations, Markdown report
citation-needed answer.json --format json       # JSON citation lists work too
citation-needed answer.md --sources refs/       # also check every number against refs/
citation-needed answer.md --sources refs/ --only numbers   # numbers only, no network
```

No API key is needed. Lookups go to Crossref and PubMed, which are free, and every response is cached on disk, so the same input against the same cache always gives the same report.

Use it as a gate in CI or a pipeline. The exit code is 1 when any listed status appears:

```bash
citation-needed answer.md --sources refs/ --fail-on fabricated,retracted,distorted,not_found
```

## Citation verdicts

| Status | Meaning |
|---|---|
| `abstained` | Empty or placeholder slot. The model declined to cite. That is the good outcome, and it is never counted as a fabrication. |
| `could_not_check` | A lookup failed (network, rate limit) or the citation has no title or identifier. Unknown is reported as unknown. |
| `fabricated` | Every index answered, and none of them knows this work. |
| `retracted` | The work exists and a retraction signal fired (Crossref, PubMed, optional Retraction Watch data). |
| `distorted` | The work exists but the metadata is materially wrong. Usually a real DOI or PMID attached to a **different** paper. |
| `matches_record` | Real, not retracted, metadata matches. Says nothing about whether it supports the claim. |

Each verdict comes with its evidence (the title the model claimed next to the title the identifier actually resolves to) and a confidence label.

## Number verdicts

| Status | Meaning |
|---|---|
| `found` | The same value appears in a source, after normalization. |
| `rounded` | The answer rounds a more precise source value ("7.8%" from "7.82%"). |
| `approximate` | The answer hedges and a source value is inside the hedge: within 10% for "about X", on the right side and within 25% for "more than X" or "up to X". |
| `unit_mismatch` | The value appears, but only with a different unit ("5 mg" in the answer, "5 g" in the source). Flagged. |
| `not_found` | No source contains this number. Flagged. |

Normalization covers thousands separators (`43,508`, `43508`, `43 508`), percent spellings, scale words and suffixes (`1.5 million`, `8B`, `3.5k`), currency, exact unit conversions (`0.5 g` = `500 mg`, `2 hours` = `120 minutes`), and ranges (`10-20%`, `8B to 120B`, `between 5 and 10 days`), which are checked end by end. It skips digits that are not claims: reference lists, citation markers like `[3]`, DOIs, URLs, list numbering, labels like "Table 2", and names like COVID-19 or qwen3.6-27b. Sources can be `.txt`, `.md`, `.csv`, `.json`, `.html`, or `.pdf` with the `[pdf]` extra. `--ignore-years` skips four-digit years, and `--number-words` also checks spelled-out numbers in the answer.

## MCP server: let the agent check itself

`citation-needed-mcp` is a stdio MCP server with two tools:

- **`check_citations(text)`** returns the verdicts above for every citation in a draft, plus a `fix` list telling the agent what to do: remove fabricated work rather than invent a replacement, correct distorted references from the resolved record.
- **`check_numbers(text, source_texts=[...], source_paths=[...])`** returns the number verdicts. Sources can be passed as strings (whatever the agent retrieved) or local paths.

It works with the official MCP Python SDK, 1.x and 2.x.

**Claude Code:**

```bash
claude mcp add citation-needed -e CITATION_NEEDED_EMAIL=you@example.org -- citation-needed-mcp
```

**Claude Desktop:** add this to `claude_desktop_config.json` (Settings, Developer, Edit Config), with the full path from `which citation-needed-mcp`:

```json
{
  "mcpServers": {
    "citation-needed": {
      "command": "/full/path/to/citation-needed-mcp",
      "env": { "CITATION_NEEDED_EMAIL": "you@example.org" }
    }
  }
}
```

Then ask for something like: "Draft the answer, run check_citations and check_numbers on it against the papers I gave you, and fix whatever they flag before you reply."

`check_numbers` reads any file path the agent passes. Only connect it to agents you would let read those files anyway.

## Why a resolving DOI is not a pass

In the study this tool comes from, **76.5% of distorted citations carried a DOI or PMID that resolved to a real but different paper.** A checker that only asks "does this DOI exist?" scores every one of those as a pass. This one compares the claimed title against the record the identifier resolves to, which is how citation 3 above is caught.

The same study is why abstention is treated as a good outcome. Asked for exactly five citations, models that cannot find five pad the list with empty slots. The study first scored those slots as fabricated; correcting that reversed one model's result and removed a headline finding. Here an empty slot is `abstained`, and there is a regression test for it.

## What is not validated

- **Support (does the paper back the claim?)** is available behind `--judge` and is **experimental**. In the study, the LLM judge reached Cohen's kappa 0.525 against 100 blind human-coded pairs, below the bar for reporting it, so the study left support unscored. Treat its labels as hints. The MCP server does not expose it.
- **Retraction detection** is a check, not an accuracy claim: the study found 92 retracted citations out of 43,508, too few to estimate recall.
- **Coverage is Crossref and PubMed.** Work those indices do not hold can come back `fabricated` with medium confidence; the report says so when a citation had no DOI or PMID. In the study, OpenAlex alone resolved 1.4% of citations on a four-source subsample. Add sources with `CITATION_NEEDED_SOURCES=crossref,pubmed,openalex,s2`.
- **Known miss from the live run:** "Attention is all you need" (NeurIPS 2017) has no Crossref or PubMed record of its own, but Crossref holds 2025 re-deposits with the same title and authors, so it comes back `distorted` for the year. The resolved record in the report shows why. A test pins this behavior.
- **The citation pipeline is no longer byte-for-byte the study's.** The live run turned up three bugs, now fixed: short titles matched longer, different papers; PubMed title search could bury the right paper; and PubMed records could carry the DOI of a paper they cite. That last bug is also in the study's harness. These fixes have not been rerun on the study's 43,508 citations.
- **The numbers check is provenance, not fact checking.** It confirms a number appears in the sources, not that it is attached to the right quantity: "fabrication was 9.31%" passes if 9.31% appears anywhere in them. Its tolerances (10% for "about", 25% for bounds) are design choices, not validated against human judgments. It does not convert percentages to proportions (`25%` vs `0.25`).

## Citing

If you use this tool, please cite the study its method comes from:

> Turk D. Cheap, private, but trustworthy? Verification harness, medical claim corpus, and full results for citation integrity in open-weight, self-hostable language models answering medical claims. Zenodo; 2026. [doi:10.5281/zenodo.22030231](https://doi.org/10.5281/zenodo.22030231)

Study code and data: [Tdeniz1/Open-Weight-LLM-Medical-Citation-Audit](https://github.com/Tdeniz1/Open-Weight-LLM-Medical-Citation-Audit). `CITATION.cff` has both entries in machine-readable form.

## License

MIT. The citation pipeline is ported from the study's harness (MIT). The source document in `examples/sources/` is the study abstract's Results paragraph, CC BY 4.0.
