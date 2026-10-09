"""The cache must never turn an outage into a permanent verdict."""
from __future__ import annotations

import requests

from citation_needed import cache


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = ""

    def json(self):
        return self._body

    def close(self):
        pass


def _fake_session(monkeypatch, responses):
    calls = []

    class S:
        def get(self, url, params=None, timeout=None):
            calls.append(params)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    monkeypatch.setattr(cache, "_session", lambda: S())
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    return calls


def _online(monkeypatch, tmp_path):
    monkeypatch.setenv("CITATION_NEEDED_CACHE", str(tmp_path))
    monkeypatch.delenv("CITATION_NEEDED_OFFLINE")


def test_transient_failures_are_not_cached_and_are_reported(monkeypatch, tmp_path):
    _online(monkeypatch, tmp_path)
    _fake_session(monkeypatch, [_Resp(503), _Resp(503), requests.ConnectionError()])
    with cache.track_failures() as failures:
        e = cache.cached_request("t", "https://api.crossref.org/works/10.1/x")
    assert e["error"] and not list(tmp_path.rglob("*.json"))
    assert len(failures) == 1


def test_definitive_404_is_cached_as_an_answer(monkeypatch, tmp_path):
    _online(monkeypatch, tmp_path)
    _fake_session(monkeypatch, [_Resp(404)])
    with cache.track_failures() as failures:
        e = cache.cached_request("t", "https://api.crossref.org/works/10.1/missing")
    assert e["error"] == "HTTP 404" and not failures
    again = cache.cached_request("t", "https://api.crossref.org/works/10.1/missing")
    assert again["from_cache"] is True


def test_contact_email_is_sent_but_never_keyed_or_stored(monkeypatch, tmp_path):
    _online(monkeypatch, tmp_path)
    monkeypatch.setenv("CITATION_NEEDED_EMAIL", "someone@example.org")
    calls = _fake_session(monkeypatch, [_Resp(200, {"ok": 1})])
    cache.cached_request("t", "https://api.crossref.org/works", {"q": "x"})
    assert calls[0]["mailto"] == "someone@example.org"
    stored = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert "someone@example.org" not in stored
    monkeypatch.delenv("CITATION_NEEDED_EMAIL")
    assert cache.cached_request("t", "https://api.crossref.org/works", {"q": "x"})["from_cache"]
