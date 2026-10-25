"""Redact email addresses from recorded API responses before committing them.

PubMed records include authors' contact emails in affiliation strings. They are
public, but nothing in the checker reads them, so recorded fixtures carry none.
Run after re-recording: python tests/fixtures/scrub.py
"""
from __future__ import annotations

import re
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

if __name__ == "__main__":
    changed = 0
    for f in sorted((Path(__file__).parent / "cache").rglob("*.json")):
        text = f.read_text(encoding="utf-8")
        new = EMAIL.sub("redacted@example.invalid", text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
    print(f"redacted emails in {changed} file(s)")
