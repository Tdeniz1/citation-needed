"""Load the source documents an answer is supposed to be grounded in.

Plain text, Markdown, CSV/TSV, JSON and HTML are read directly. PDFs are read when
the optional `pypdf` package is installed (`pip install "citation-needed[pdf]"`);
otherwise they are reported as skipped, never silently ignored.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
                 ".yaml", ".yml", ".xml", ".tex", ".bib"}
HTML_SUFFIXES = {".html", ".htm"}


@dataclass
class SourceDoc:
    name: str
    text: str


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def _pdf_text(path: Path) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def load_sources(paths: list[str | Path]) -> tuple[list[SourceDoc], list[str]]:
    """Read every supported file under `paths` (files or directories, recursively).

    Returns (documents, skipped) where `skipped` explains each file that was not read.
    Hidden files and directories are ignored. Order is deterministic (sorted paths).
    """
    docs: list[SourceDoc] = []
    skipped: list[str] = []
    files: list[Path] = []
    for p in map(Path, paths):
        p = p.expanduser()
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file()
                            and not any(part.startswith(".") for part in f.relative_to(p).parts))
        elif p.is_file():
            files.append(p)
        else:
            skipped.append(f"{p}: not found")
    for f in files:
        suffix = f.suffix.lower()
        if suffix in TEXT_SUFFIXES or suffix == "":
            try:
                docs.append(SourceDoc(str(f), f.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                skipped.append(f"{f}: not UTF-8 text")
        elif suffix in HTML_SUFFIXES:
            docs.append(SourceDoc(str(f), html_to_text(f.read_text(encoding="utf-8", errors="replace"))))
        elif suffix == ".pdf":
            text = _pdf_text(f)
            if text is None:
                skipped.append(f"{f}: PDF support needs `pip install \"citation-needed[pdf]\"`")
            else:
                docs.append(SourceDoc(str(f), text))
        else:
            skipped.append(f"{f}: unsupported file type {suffix}")
    return docs, skipped
