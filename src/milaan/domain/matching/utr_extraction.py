"""UTR extraction from free-text bank narration (Implementation Plan §6.2, task 2.2).

Bank statement `narration` fields are unstructured — a UTR may already be broken out into
`utr_extracted` by the bank's own export, or it may only exist embedded in free text like
"NEFT CR UTR20260105001 SETTLEMENT" or "IMPS/UTR/20260105ABCDE1234/JOHN DOE". This module
provides the regex fallback for the latter case, plus a normalisation step so "utr123",
"UTR 123", and "utr-123" all compare equal.
"""

from __future__ import annotations

import re

# Matches a "UTR"/"RRN"/"REFERENCE" keyword followed by an alphanumeric token that
# contains at least one digit (a real UTR is never pure letters). `\b` before the keyword
# prevents matching a keyword embedded inside a longer unrelated word; requiring a digit in
# the captured token — rather than a trailing `\b` — is what correctly rejects plain
# English text like "...with no reference" while still matching UTRs glued directly to
# digits with no separator ("UTR20260105001"), which a trailing `\b` would have broken.
_UTR_PATTERN = re.compile(
    r"\b(?:UTR|RRN|REFERENCE)[\s/:#-]*([A-Z0-9]*\d[A-Z0-9]{3,24})", re.IGNORECASE
)


def normalize_utr(raw: str) -> str:
    return re.sub(r"[\s\-]+", "", raw.strip().upper())


def extract_utr(narration: str, utr_extracted: str | None = None) -> str | None:
    """Prefer a bank-supplied `utr_extracted` field when present and non-empty; only fall
    back to regex extraction from narration when it isn't (the common real-world case
    where the bank's own export never separated it out)."""
    if utr_extracted and utr_extracted.strip():
        return normalize_utr(utr_extracted)
    match = _UTR_PATTERN.search(narration)
    if match:
        return normalize_utr(match.group(1))
    return None
