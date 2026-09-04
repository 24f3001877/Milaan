"""LLM adapter error types."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM adapter errors."""


class LLMDisabledError(LLMError):
    """Raised immediately when LLM_MODE=disabled. Callers use this to trigger graceful
    degradation (C7) — the deterministic pipeline continues without triage."""


class LLMCacheMissError(LLMError):
    """Raised when LLM_MODE=cached and no cached response exists for this exact prompt.
    Deliberately a hard failure, not a silent fallback to live mode — cached mode's whole
    point is zero network calls and byte-identical CI runs (Appflow §4.2)."""


class LLMValidationError(LLMError):
    """Raised when the model's response fails schema validation on every retry attempt.
    Callers escalate to human review rather than trust an unparseable or out-of-schema
    response (TRD §2.4)."""
