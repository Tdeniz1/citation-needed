"""Deterministic on-disk cache and polite HTTP client.

Every response the checker sees goes through `cached_request` and is stored as one
JSON file keyed by a hash of (namespace, url, params). A rerun reads from disk, so
the same input against the same cache always produces the same report.

Changes from the study's cache.py, each because a tool has different failure
modes than a one-off batch run:

* Transient failures (network errors, 429, 5xx) are NOT written to the cache. The
  study could rerun failed cells by hand; a tool that cached a Crossref outage would
  report every citation in it as fabricated, forever.
* Every request that could not get a definitive answer is recorded in
  `lookup_failures()`, so the classifier can say "could not check" instead of
  "fabricated" when the indices were unreachable.
* Contact details (mailto, email, api_key) are added when sending and kept out of the
  cache key, so caches and test fixtures are portable between users.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import requests

from citation_needed import config

_RETRYABLE = (429, 500, 502, 503, 504)
# Parameters that identify the caller rather than the question.
_IDENTITY_PARAMS = ("mailto", "email", "api_key", "tool")

_FAILURES: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "citation_needed_failures", default=None
)


@contextlib.contextmanager
def track_failures() -> Iterator[list[dict]]:
    """Collect every lookup in this block that ended without a definitive answer."""
    bucket: list[dict] = []
    token = _FAILURES.set(bucket)
    try:
        yield bucket
    finally:
        _FAILURES.reset(token)


def _record_failure(namespace: str, url: str, error: str) -> None:
    bucket = _FAILURES.get()
    if bucket is not None:
        bucket.append({"namespace": namespace, "url": url, "error": error})


_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({"User-Agent": config.user_agent(), "Accept": "application/json"})
        _SESSION = s
    return _SESSION


# NCBI per-IP token bucket, shared by all threads.
_NCBI_LOCK = threading.Lock()
_NCBI_NEXT = [0.0]


def _ncbi_throttle() -> None:
    interval = 1.0 / max(config.ncbi_rps(), 0.1)
    with _NCBI_LOCK:
        now = time.time()
        slot = max(now, _NCBI_NEXT[0])
        _NCBI_NEXT[0] = slot + interval
    wait = slot - now
    if wait > 0:
        time.sleep(wait)


def _key(namespace: str, url: str, params: dict | None) -> str:
    stable = {k: v for k, v in (params or {}).items() if k not in _IDENTITY_PARAMS}
    payload = json.dumps({"ns": namespace, "url": url, "params": stable},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _send_params(url: str, params: dict | None) -> dict:
    out = dict(params or {})
    email = config.contact_email()
    if "crossref.org" in url or "openalex.org" in url:
        if email:
            out["mailto"] = email
    elif "ncbi.nlm.nih.gov" in url:
        out["tool"] = config.TOOL_NAME
        if email:
            out["email"] = email
        if config.ncbi_api_key():
            out["api_key"] = config.ncbi_api_key()
    elif "unpaywall.org" in url:
        # Unpaywall refuses requests without an email; callers skip it when unset.
        if email:
            out["email"] = email
    return out


def _is_definitive(status: int) -> bool:
    """A response worth caching: success, or a 4xx that will not change on retry."""
    return 200 <= status < 300 or (400 <= status < 500 and status != 429)


def cached_request(namespace: str, url: str, params: dict | None = None, *,
                   expect: str = "json") -> dict[str, Any]:
    """Return {status, body, error, fetched_at, from_cache}, hitting the network on a miss.

    `error` is None for a usable 2xx body. A definitive 4xx (for example Crossref's 404
    for a DOI that does not exist) is cached and returned with `error` set to the
    status; that is an answer, not a failure. Transient failures are returned with
    `error` set and are never cached.
    """
    key = _key(namespace, url, params)
    path = config.cache_dir() / key[:2] / f"{key}.json"
    if path.exists():
        entry = json.loads(path.read_text())
        entry["from_cache"] = True
        return entry

    entry: dict[str, Any] = {"namespace": namespace, "url": url,
                             "params": {k: v for k, v in (params or {}).items()
                                        if k not in _IDENTITY_PARAMS},
                             "fetched_at": None, "status": None, "body": None, "error": None,
                             "from_cache": False}
    if config.offline():
        entry["error"] = "offline-cache-miss"
        _record_failure(namespace, url, entry["error"])
        return entry

    is_ncbi = "ncbi.nlm.nih.gov" in url
    for attempt in range(config.MAX_RETRIES):
        try:
            if is_ncbi:
                _ncbi_throttle()
            resp = _session().get(url, params=_send_params(url, params),
                                  timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            entry["error"] = f"network: {exc.__class__.__name__}"
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.RETRY_BACKOFF * (attempt + 1))
                continue
            break
        entry["status"] = resp.status_code
        entry["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if resp.status_code in _RETRYABLE:
            entry["error"] = f"HTTP {resp.status_code}"
            resp.close()  # an unread error response pins its pooled connection
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.RETRY_BACKOFF * (attempt + 1))
                continue
            break
        if expect == "json":
            try:
                entry["body"], entry["error"] = resp.json(), None
            except ValueError:
                entry["body"], entry["error"] = resp.text, "non-json-body"
        else:
            entry["body"], entry["error"] = resp.text, None
        if resp.status_code >= 400:
            entry["error"] = f"HTTP {resp.status_code}"
        break

    if entry["status"] is not None and _is_definitive(entry["status"]) \
            and entry["error"] != "non-json-body":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in entry.items() if k != "from_cache"},
                                   ensure_ascii=False))
    else:
        _record_failure(namespace, url, entry["error"] or "unknown")
    return entry
