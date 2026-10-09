"""Every knob in one place. Nothing here makes a network call.

Thresholds are the ones the original study used and validated
(citation-integrity-openweight-llms, code/config.py). Change them and you are no
longer running the method the paper reports.
"""
from __future__ import annotations

import os
from pathlib import Path

from citation_needed import __version__

# --------------------------------------------------------------------------- #
# Where cached API responses live. Same input + same cache = same report.     #
# --------------------------------------------------------------------------- #

def cache_dir() -> Path:
    env = os.environ.get("CITATION_NEEDED_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "citation-needed"


def offline() -> bool:
    """CITATION_NEEDED_OFFLINE=1: answer only from cache, never touch the network."""
    return os.environ.get("CITATION_NEEDED_OFFLINE", "").strip() not in ("", "0", "false")


# --------------------------------------------------------------------------- #
# Polite-pool etiquette. Crossref, OpenAlex and NCBI ask clients to identify   #
# themselves with a contact email. It belongs to whoever runs the tool, so it  #
# comes from the environment and is never hard-coded.                          #
# --------------------------------------------------------------------------- #

PROJECT_URL = "https://github.com/Tdeniz1/citation-needed"
TOOL_NAME = "citation_needed"


def contact_email() -> str | None:
    return os.environ.get("CITATION_NEEDED_EMAIL") or None


def user_agent() -> str:
    email = contact_email()
    contact = f"; mailto:{email}" if email else ""
    return f"citation-needed/{__version__} (+{PROJECT_URL}{contact})"


def ncbi_api_key() -> str | None:
    return os.environ.get("NCBI_API_KEY") or None


# NCBI allows 3 req/s per IP without a key and 10 with one; stay just under.
def ncbi_rps() -> float:
    return 9.0 if ncbi_api_key() else 2.7


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

CROSSREF_BASE = "https://api.crossref.org/works"
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
OPENALEX_BASE = "https://api.openalex.org/works"
S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"

# The study ran existence on Crossref + PubMed only: OpenAlex is metered and
# Semantic Scholar's shared pool throttles hard. On the study's 776-citation
# four-source subsample, S2 was never the sole resolver and OpenAlex was the sole
# resolver for 1.4%, so dropping them can over-count fabrication by about that
# much. Add sources with CITATION_NEEDED_SOURCES=crossref,pubmed,openalex,s2.
DEFAULT_SOURCES = ("crossref", "pubmed")
ALL_SOURCES = ("crossref", "pubmed", "openalex", "s2")


def existence_sources() -> tuple[str, ...]:
    env = os.environ.get("CITATION_NEEDED_SOURCES")
    if not env:
        return DEFAULT_SOURCES
    picked = tuple(s.strip().lower() for s in env.split(",") if s.strip())
    unknown = set(picked) - set(ALL_SOURCES)
    if unknown:
        raise ValueError(f"unknown source(s) in CITATION_NEEDED_SOURCES: {sorted(unknown)}")
    return picked


# Optional local Retraction Watch CSV (free from Crossref). Without it the
# retraction check still runs on Crossref update-to relations and PubMed
# publication types.
def retraction_watch_csv() -> Path | None:
    env = os.environ.get("CITATION_NEEDED_RETRACTION_WATCH")
    return Path(env).expanduser() if env else None


# --------------------------------------------------------------------------- #
# Matching thresholds (rapidfuzz token_set_ratio, 0-100)                       #
# --------------------------------------------------------------------------- #

TITLE_SIM_THRESHOLD = 90.0   # title match needs this plus one shared surname
TITLE_SIM_STRONG = 95.0      # a title this close matches on its own
MIN_AUTHOR_OVERLAP = 1
SEARCH_ROWS = 5              # candidates per index when matching by title

YEAR_MINOR_TOL = 1           # off by one year is minor, more is major
VENUE_SIM_MINOR = 60.0

# --------------------------------------------------------------------------- #
# Networking                                                                   #
# --------------------------------------------------------------------------- #

HTTP_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
