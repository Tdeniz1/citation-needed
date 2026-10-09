# Ported from citation-integrity-openweight-llms (MIT, Deniz Turk 2026),
# code/harness/clients.py. Logic unchanged unless a comment says otherwise.
"""Cached clients for every index the harness queries, each normalized to a common
canonical-record schema so downstream modules never touch a raw API payload.

Canonical record (dict):
    source      one of crossref|pubmed|openalex|s2
    title       str
    authors     list[str] of surnames (lowercased)
    year        int | None
    venue       str | None
    doi         str | None (lowercased, bare)
    pmid        str | None
    is_retracted  bool | None   (only OpenAlex sets this authoritatively)
    pub_types   list[str]       (PubMed publication types)
    crossref_update_to  list[dict]   (Crossref update-to relations)
    abstract    str | None
    pmcid       str | None
    raw_ok      bool            (True when the source returned a usable record)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from citation_needed import config
from citation_needed.cache import cached_request
from citation_needed.citations.textutil import clean_doi, surname_set
from citation_needed.config import (
    CROSSREF_BASE,
    EPMC_FULLTEXT,
    EPMC_SEARCH,
    OPENALEX_BASE,
    PUBMED_EFETCH,
    PUBMED_ESEARCH,
    S2_BASE,
    SEARCH_ROWS,
    UNPAYWALL_BASE,
)

# --------------------------------------------------------------------------- #
# Crossref                                                                     #
# --------------------------------------------------------------------------- #

def _crossref_norm(msg: dict) -> dict:
    title = (msg.get("title") or [None])
    title = title[0] if title else None
    container = msg.get("container-title") or []
    year = None
    for k in ("published-print", "published-online", "published", "issued"):
        dp = (msg.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            year = int(dp[0][0])
            break
    authors = []
    for a in msg.get("author") or []:
        fam = a.get("family")
        if fam:
            authors.append(fam)
        elif a.get("name"):
            authors.append(a["name"])
    abstract = msg.get("abstract")
    if abstract:
        abstract = re.sub(r"<[^>]+>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip()
    return {
        "source": "crossref",
        "title": title,
        "authors": sorted(surname_set(authors)),
        "year": year,
        "venue": container[0] if container else None,
        "doi": clean_doi(msg.get("DOI")),
        "pmid": None,
        "is_retracted": None,
        "pub_types": [msg.get("type")] if msg.get("type") else [],
        "crossref_update_to": msg.get("update-to") or [],
        "crossref_relation": list((msg.get("relation") or {}).keys()),
        "abstract": abstract,
        "pmcid": None,
        "raw_ok": bool(title),
    }


def crossref_by_doi(doi: str) -> dict | None:
    doi = clean_doi(doi)
    if not doi:
        return None
    e = cached_request("crossref_doi", f"{CROSSREF_BASE}/{doi}")
    if e.get("error") or not isinstance(e.get("body"), dict):
        return None
    msg = e["body"].get("message")
    return _crossref_norm(msg) if msg else None


def crossref_search(title: str, author: str | None = None) -> list[dict]:
    params = {"query.bibliographic": title, "rows": SEARCH_ROWS,
              "select": "DOI,title,author,container-title,issued,published-print,type,update-to,relation,abstract"}
    if author:
        params["query.author"] = author
    e = cached_request("crossref_search", CROSSREF_BASE, params)
    if e.get("error") or not isinstance(e.get("body"), dict):
        return []
    items = (e["body"].get("message") or {}).get("items") or []
    return [_crossref_norm(m) for m in items]


# --------------------------------------------------------------------------- #
# PubMed E-utilities                                                           #
# --------------------------------------------------------------------------- #

def _pubmed_parse_article(art: ET.Element) -> dict:
    def txt(path):
        el = art.find(path)
        return el.text if el is not None else None

    pmid = txt(".//PMID")
    title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else None
    year = txt(".//JournalIssue/PubDate/Year") or txt(".//JournalIssue/PubDate/MedlineDate")
    if year:
        m = re.search(r"(1[89]\d{2}|20\d{2})", year)
        year = int(m.group(1)) if m else None
    journal = txt(".//Journal/Title") or txt(".//MedlineJournalInfo/MedlineTA")
    authors = [a.text for a in art.findall(".//AuthorList/Author/LastName") if a.text]
    pub_types = [pt.text for pt in art.findall(".//PublicationTypeList/PublicationType") if pt.text]
    # retraction cross-references (RefType attr on CommentsCorrections)
    ref_types = [cc.get("RefType") for cc in art.findall(".//CommentsCorrections")]
    doi = None
    for aid in art.findall(".//ArticleId"):
        if aid.get("IdType") == "doi" and aid.text:
            doi = clean_doi(aid.text)
    pmcid = None
    for aid in art.findall(".//ArticleId"):
        if aid.get("IdType") == "pmc" and aid.text:
            pmcid = aid.text
    abstract_parts = [ab.text or "" for ab in art.findall(".//Abstract/AbstractText")]
    abstract = " ".join(p for p in abstract_parts if p).strip() or None
    return {
        "source": "pubmed",
        "title": title.strip() if title else None,
        "authors": sorted(surname_set(authors)),
        "year": year,
        "venue": journal,
        "doi": doi,
        "pmid": pmid,
        "is_retracted": None,
        "pub_types": pub_types,
        "comments_corrections_reftypes": ref_types,
        "crossref_update_to": [],
        "abstract": abstract,
        "pmcid": pmcid,
        "raw_ok": bool(title and pmid),
    }


def pubmed_efetch(pmid: str) -> dict | None:
    pmid = str(pmid).strip()
    if not pmid.isdigit():
        return None
    e = cached_request(
        "pubmed_efetch", PUBMED_EFETCH,
        {"db": "pubmed", "id": pmid, "retmode": "xml"},
        expect="text",
    )
    if e.get("error") or not isinstance(e.get("body"), str):
        return None
    try:
        root = ET.fromstring(e["body"])
    except ET.ParseError:
        return None
    art = root.find(".//PubmedArticle")
    return _pubmed_parse_article(art) if art is not None else None


def pubmed_esearch(term: str, retmax: int = SEARCH_ROWS) -> list[str]:
    e = cached_request(
        "pubmed_esearch", PUBMED_ESEARCH,
        {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax},
    )
    if e.get("error") or not isinstance(e.get("body"), dict):
        return []
    return (e["body"].get("esearchresult") or {}).get("idlist") or []


def pubmed_search_title(title: str) -> list[dict]:
    pmids = pubmed_esearch(f"{title}[Title]")
    if not pmids:
        # fall back to a looser all-fields search
        pmids = pubmed_esearch(title)
    out = []
    for pmid in pmids[:SEARCH_ROWS]:
        rec = pubmed_efetch(pmid)
        if rec:
            out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# OpenAlex                                                                     #
# --------------------------------------------------------------------------- #

def _openalex_norm(w: dict) -> dict:
    doi = w.get("doi")
    ids = w.get("ids") or {}
    pmid = None
    if ids.get("pmid"):
        pmid = re.sub(r"\D", "", ids["pmid"])
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    authors = [
        (a.get("author") or {}).get("display_name")
        for a in (w.get("authorships") or [])
    ]
    oa = w.get("open_access") or {}
    return {
        "source": "openalex",
        "title": w.get("title") or w.get("display_name"),
        "authors": sorted(surname_set([a for a in authors if a])),
        "year": w.get("publication_year"),
        "venue": venue,
        "doi": clean_doi(doi),
        "pmid": pmid,
        "is_retracted": w.get("is_retracted"),
        "pub_types": [w.get("type")] if w.get("type") else [],
        "crossref_update_to": [],
        "abstract": _openalex_abstract(w),
        "pmcid": None,
        "oa_pdf_url": oa.get("oa_url"),
        "raw_ok": bool(w.get("title") or w.get("display_name")),
    }


def _openalex_abstract(w: dict) -> str | None:
    inv = w.get("abstract_inverted_index")
    if not inv:
        return None
    positions = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions)) or None


def openalex_by_id(doi: str | None = None, pmid: str | None = None) -> dict | None:
    if doi:
        doi = clean_doi(doi)
        e = cached_request("openalex_id", f"{OPENALEX_BASE}/https://doi.org/{doi}")
    elif pmid:
        e = cached_request("openalex_id", f"{OPENALEX_BASE}/pmid:{pmid}")
    else:
        return None
    if e.get("error") or not isinstance(e.get("body"), dict) or e["body"].get("id") is None:
        return None
    return _openalex_norm(e["body"])


def openalex_search(title: str) -> list[dict]:
    e = cached_request(
        "openalex_search", OPENALEX_BASE,
        {"filter": f"title.search:{title}", "per_page": SEARCH_ROWS},
    )
    if e.get("error") or not isinstance(e.get("body"), dict):
        return []
    return [_openalex_norm(w) for w in (e["body"].get("results") or [])]


def openalex_search_relevance(query: str) -> list[dict]:
    """Full-text relevance search (title+abstract+fulltext) for candidate RETRIEVAL.

    Distinct from openalex_search (title-only, used by the existence check for matching a
    known title). This has far higher recall on free-text claims, so it backs the rag /
    tool_use retrieval where the query is a claim, not a title.
    """
    e = cached_request(
        "openalex_relevance", OPENALEX_BASE,
        {"search": query, "per_page": SEARCH_ROWS},
    )
    if e.get("error") or not isinstance(e.get("body"), dict):
        return []
    return [_openalex_norm(w) for w in (e["body"].get("results") or [])]


# --------------------------------------------------------------------------- #
# Semantic Scholar                                                            #
# --------------------------------------------------------------------------- #

_S2_FIELDS = "title,year,venue,externalIds,authors,abstract,publicationTypes"


def _s2_norm(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    return {
        "source": "s2",
        "title": p.get("title"),
        "authors": sorted(surname_set([a.get("name") for a in (p.get("authors") or [])])),
        "year": p.get("year"),
        "venue": p.get("venue"),
        "doi": clean_doi(ext.get("DOI")),
        "pmid": str(ext["PubMed"]) if ext.get("PubMed") else None,
        "is_retracted": None,
        "pub_types": p.get("publicationTypes") or [],
        "crossref_update_to": [],
        "abstract": p.get("abstract"),
        "pmcid": ext.get("PubMedCentral"),
        "raw_ok": bool(p.get("title")),
    }


def s2_by_id(doi: str | None = None, pmid: str | None = None) -> dict | None:
    if doi:
        pid = f"DOI:{clean_doi(doi)}"
    elif pmid:
        pid = f"PMID:{pmid}"
    else:
        return None
    e = cached_request("s2_id", f"{S2_BASE}/{pid}", {"fields": _S2_FIELDS})
    if e.get("error") or not isinstance(e.get("body"), dict) or not e["body"].get("title"):
        return None
    return _s2_norm(e["body"])


def s2_search(title: str) -> list[dict]:
    e = cached_request(
        "s2_search", f"{S2_BASE}/search",
        {"query": title, "limit": SEARCH_ROWS, "fields": _S2_FIELDS},
    )
    if e.get("error") or not isinstance(e.get("body"), dict):
        return []
    return [_s2_norm(p) for p in (e["body"].get("data") or [])]


# --------------------------------------------------------------------------- #
# Europe PMC + Unpaywall (support-check fetch layer)                           #
# --------------------------------------------------------------------------- #

def epmc_core(doi: str | None = None, pmid: str | None = None) -> dict | None:
    if pmid:
        query = f"EXT_ID:{pmid} AND SRC:MED"
    elif doi:
        query = f'DOI:"{clean_doi(doi)}"'
    else:
        return None
    e = cached_request("epmc_search", EPMC_SEARCH,
                       {"query": query, "format": "json", "resultType": "core"})
    if e.get("error") or not isinstance(e.get("body"), dict):
        return None
    results = ((e["body"].get("resultList") or {}).get("result") or [])
    return results[0] if results else None


def epmc_fulltext_xml(pmcid: str) -> str | None:
    pmcid = pmcid if str(pmcid).upper().startswith("PMC") else f"PMC{pmcid}"
    e = cached_request("epmc_fulltext", EPMC_FULLTEXT.format(pmcid=pmcid), None, expect="text")
    if e.get("error") or not isinstance(e.get("body"), str) or "<" not in e["body"]:
        return None
    return e["body"]


def unpaywall(doi: str) -> dict | None:
    doi = clean_doi(doi)
    # Unpaywall rejects anonymous requests, so it only runs when the user set an email.
    if not doi or not config.contact_email():
        return None
    e = cached_request("unpaywall", f"{UNPAYWALL_BASE}/{doi}")
    if e.get("error") or not isinstance(e.get("body"), dict):
        return None
    return e["body"]
