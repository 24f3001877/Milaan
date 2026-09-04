"""Domain error types. Zero I/O, zero framework — plain exceptions the adapters layer
catches and translates into HTTP problem-detail responses or ingest-summary entries."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class RowValidationError(DomainError):
    """A single ingested row failed validation (missing required field, bad type,
    value outside the allowed domain). Callers accumulate these per-row rather than
    aborting the whole file — one bad row shouldn't block the other 4,999."""
