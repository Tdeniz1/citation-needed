"""Numbers checker: does every number in an answer appear in its source documents?"""
from citation_needed.numbers.check import FLAGGED, STATUSES, check_numbers
from citation_needed.numbers.extract import extract_numbers
from citation_needed.numbers.sources import SourceDoc, load_sources

__all__ = ["FLAGGED", "STATUSES", "SourceDoc", "check_numbers", "extract_numbers", "load_sources"]
