# citation-needed

**Check whether an LLM's citations are real, unretracted, and actually the paper they claim to be.**

Hand it an LLM answer. For every citation it reports one of:

| Status | Meaning |
|---|---|
| `abstained` | Empty or placeholder slot. The model declined to cite. That is the good outcome, and it is never counted as a fabrication. |
| `could_not_check` | A lookup failed (network, rate limit) or the citation has no title or identifier. Unknown is reported as unknown. |
| `fabricated` | Every index answered, and none of them knows this work. |
| `retracted` | The work exists and a retraction signal fired (Crossref, PubMed, optional Retraction Watch data). |
| `distorted` | The work exists but the metadata is materially wrong. Usually a real DOI or PMID attached to a **different** paper. |
| `matches_record` | Real, not retracted, metadata matches. Says nothing about whether it supports the claim. |

Each verdict comes with its evidence (the title the model claimed next to the title the identifier actually resolves to) and a confidence label.

> Status: early development. Not yet on PyPI.

## Quick start

```bash
pip install -e .
export CITATION_NEEDED_EMAIL=you@example.org   # polite-pool contact for Crossref/NCBI (recommended)
citation-needed answer.md                       # Markdown report
citation-needed answer.json --format json       # machine-readable
```

No API key is needed. Lookups go to Crossref and PubMed, which are free, and every response is cached on disk, so the same input against the same cache always gives the same report.

## Why a resolving DOI is not a pass

This tool is the checking pipeline from a study of 43,508 citations produced by six open-weight models (Turk 2026, [doi:10.5281/zenodo.22030231](https://doi.org/10.5281/zenodo.22030231)). In that study, **76.5% of distorted citations carried a DOI or PMID that resolved to a real but different paper.** A checker that only asks "does this DOI exist?" scores every one of those as a pass. This one compares the claimed title against the record the identifier resolves to.

## What is not validated

- **Support (does the paper back the claim?)** is available behind `--judge` and is **experimental**. In the study, the LLM judge reached Cohen's kappa 0.525 against 100 blind human-coded pairs, below the bar for reporting it, so the study left support unscored. Treat its labels as hints.
- **Retraction detection** is a check, not an accuracy claim: the study found 92 retracted citations out of 43,508, too few to estimate recall.
- **Default sources are Crossref and PubMed.** In the study, OpenAlex alone resolved 1.4% of citations on a four-source subsample, so `fabricated` can be slightly over-counted. Add sources with `CITATION_NEEDED_SOURCES=crossref,pubmed,openalex,s2`.

## Citing

If you use this tool, please cite the study it comes from (see `CITATION.cff`).

## License

MIT for code. Ported from [citation-integrity-openweight-llms](https://github.com/Tdeniz1/citation-integrity-openweight-llms) (MIT).
