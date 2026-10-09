# Ported from citation-integrity-openweight-llms (MIT, Deniz Turk 2026),
# code/harness/support.py. Ollama judge dropped; otherwise unchanged.
"""Step 5: support check. EXPERIMENTAL.

In the original study this judge reached Cohen's kappa 0.525 against 100 blind
human-coded pairs, below the gate for reporting it, so the study left support
unscored. It ships here so people can try it and help validate it, but a label from
it is a hint, not a verdict. Needs `pip install citation-needed[judge]` and
ANTHROPIC_API_KEY.

Original module notes:

For a resolved, non-retracted, non-distorted citation, decide whether the source
actually supports the claim it was attached to.

  1. Fetch evidence text: abstract (from the resolved canonical record, else Europe PMC),
     and where open access is available (Unpaywall / Europe PMC full-text XML) fetch a
     full-text excerpt.
  2. Hand (claim, title, abstract [+ full-text excerpt]) to a disclosed judge model and
     ask for supports / unrelated / contradicts.

The judge call is cached like every other external call, so reruns are deterministic.
The classifier only calls this when the user asks for it (`--judge`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from citation_needed import config
from citation_needed.citations import clients

# Used only if listing models fails; the live models list is preferred.
JUDGE_MODEL_FALLBACK = "claude-sonnet-5-5"


def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None

_SUPPORT_LABELS = {"supports", "unrelated", "contradicts"}


# --------------------------------------------------------------------------- #
# Evidence gathering                                                           #
# --------------------------------------------------------------------------- #

def gather_evidence(canonical: dict, max_fulltext_chars: int = 6000) -> dict:
    doi = canonical.get("doi")
    pmid = canonical.get("pmid")
    pmcid = canonical.get("pmcid")
    abstract = canonical.get("abstract")
    used = []

    if abstract:
        used.append("canonical_abstract")
    if not abstract and (pmid or doi):
        core = clients.epmc_core(doi=doi, pmid=pmid)
        if core and core.get("abstractText"):
            abstract = re.sub(r"<[^>]+>", " ", core["abstractText"])
            abstract = re.sub(r"\s+", " ", abstract).strip()
            used.append("europepmc_abstract")
            if not pmcid and core.get("pmcid"):
                pmcid = core["pmcid"]

    fulltext = None
    is_oa = None
    if doi:
        up = clients.unpaywall(doi)
        if up is not None:
            is_oa = up.get("is_oa")
    if pmcid:
        xml = clients.epmc_fulltext_xml(pmcid)
        if xml:
            body = re.sub(r"<[^>]+>", " ", xml)
            body = re.sub(r"\s+", " ", body).strip()
            fulltext = body[:max_fulltext_chars]
            used.append("europepmc_fulltext")

    return {
        "abstract": abstract,
        "fulltext_excerpt": fulltext,
        "is_open_access": is_oa,
        "evidence_sources": used,
        "has_evidence": bool(abstract or fulltext),
    }


# --------------------------------------------------------------------------- #
# Judge model                                                                  #
# --------------------------------------------------------------------------- #

def _resolve_judge_model(client) -> str:
    """Query the Anthropic models list at run time and pick a Sonnet-class judge.

    The brief forbids hard-coding model names from memory; this resolves the exact
    current identifier and records it. Falls back to a labelled default if listing
    fails (recorded so the disclosure stays honest).
    """
    try:
        models = client.models.list(limit=50)
        ids = [m.id for m in models.data]
        for pref in ("sonnet",):
            cand = [i for i in ids if pref in i.lower()]
            if cand:
                # newest sonnet id sorts last lexically for date-suffixed ids
                return sorted(cand)[-1]
        if ids:
            return ids[0]
    except Exception:
        pass
    return JUDGE_MODEL_FALLBACK


_JUDGE_PROMPT = (
    "You are grading whether a scientific source supports a medical claim. "
    "You are given the CLAIM and the source's TITLE and ABSTRACT (and possibly a "
    "full-text excerpt). Decide strictly from the provided text.\n\n"
    "Answer with a single JSON object: {{\"label\": one of \"supports\"|"
    "\"unrelated\"|\"contradicts\", \"rationale\": \"one sentence\"}}.\n"
    "- supports: the source's findings substantiate the claim.\n"
    "- contradicts: the source's findings are inconsistent with the claim.\n"
    "- unrelated: the source does not address the claim.\n\n"
    "CLAIM: {claim}\n\nSOURCE TITLE: {title}\n\nABSTRACT: {abstract}\n\n"
    "FULLTEXT EXCERPT: {fulltext}\n"
)


def _judge_cache_path(claim: str, canonical: dict, model: str):
    key = hashlib.sha256(
        json.dumps({"c": claim, "d": canonical.get("doi"), "p": canonical.get("pmid"),
                    "t": canonical.get("title"), "m": model}, sort_keys=True).encode()
    ).hexdigest()
    d = config.cache_dir() / "judge"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"



def judge_support(claim: str, canonical: dict, evidence: dict,
                  model: str | None = None) -> dict:
    key = anthropic_key()
    if not key:
        return {"label": None, "error": "no-anthropic-key", "judge_model": None}
    try:
        import anthropic
    except ImportError:
        return {"label": None, "error": "anthropic-sdk-missing", "judge_model": None}

    client = anthropic.Anthropic(api_key=key)
    model = model or os.environ.get("CITATION_NEEDED_JUDGE_MODEL") or _resolve_judge_model(client)

    cache_path = _judge_cache_path(claim, canonical, model)
    if cache_path.exists():
        out = json.loads(cache_path.read_text())
        out["from_cache"] = True
        return out

    prompt = _JUDGE_PROMPT.format(
        claim=claim,
        title=canonical.get("title") or "(unknown)",
        abstract=(evidence.get("abstract") or "(no abstract available)")[:8000],
        fulltext=(evidence.get("fulltext_excerpt") or "(no full text)")[:6000],
    )
    try:
        msg_kwargs = dict(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            # temperature=0 for determinism on models that still accept it
            resp = client.messages.create(temperature=0, **msg_kwargs)
        except Exception as exc:
            # newer models (e.g. sonnet-5 class) deprecate temperature -> retry without it.
            # determinism across reruns is preserved by the on-disk judge cache regardless.
            if "temperature" in str(exc).lower():
                resp = client.messages.create(**msg_kwargs)
            else:
                raise
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else {}
        label = (parsed.get("label") or "").strip().lower()
        if label not in _SUPPORT_LABELS:
            label = None
        out = {"label": label, "rationale": parsed.get("rationale"),
               "judge_model": model, "raw": text, "error": None}
    except Exception as exc:  # network / API error
        out = {"label": None, "judge_model": model, "error": str(exc)}

    if out.get("error") is None:
        cache_path.write_text(json.dumps(out, ensure_ascii=False))
    out["from_cache"] = False
    return out


def check_support(claim: str, canonical: dict, model: str | None = None) -> dict:
    evidence = gather_evidence(canonical)
    result = {"evidence": {k: v for k, v in evidence.items() if k != "abstract"},
              "abstract_chars": len(evidence.get("abstract") or ""),
              "judge": None, "support_label": None}
    if evidence["has_evidence"]:
        judge = judge_support(claim, canonical, evidence, model=model)
        result["judge"] = judge
        result["support_label"] = judge.get("label")
    return result
