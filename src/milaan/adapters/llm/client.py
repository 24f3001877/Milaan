"""LLMClient abstraction (Implementation Plan §6.2, task 2.10).

Three modes (TRD §2.1, Appflow §4.4):
  - live: calls the real provider (Anthropic's Messages API — api.anthropic.com is the
    only LLM domain in the allowed egress list). Writes every response to the disk cache
    on the way out, so a live run can be replayed later in cached mode.
  - cached: reads the disk cache only, never touches the network. This is what CI uses
    (Appflow §4.2 job 3: "zero API cost, no network flake, deterministic"). A miss is a
    hard error (LLMCacheMissError), not a silent live fallback.
  - disabled: raises immediately. Callers use this to trigger graceful degradation (C7) —
    the deterministic pipeline keeps running; whatever needed the LLM becomes plain,
    uncategorised.

Every call is schema-validated (Pydantic) with bounded retry-then-escalate (TRD §2.4): a
response that doesn't parse gets one corrective retry, then raises LLMValidationError for
the caller to escalate to human review rather than trust a malformed proposal.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from milaan.adapters.llm.cache import DiskCache, prompt_hash
from milaan.adapters.llm.errors import LLMCacheMissError, LLMDisabledError, LLMValidationError

T = TypeVar("T", bound=BaseModel)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_VALIDATION_RETRIES = 2


@dataclass
class LLMCallRecord:
    """Mirrors the `llm_call` DB table (Schema §5.4) minus DB-generated id/run_id, which
    the caller supplies when persisting. Every AI decision traceable to its exact prompt
    version and cost."""

    purpose: str
    model: str
    prompt_version: str
    prompt_sha256: str
    request_payload: dict
    response_payload: dict | None
    input_tokens: int | None
    output_tokens: int | None
    cost_micros: int | None
    latency_ms: int | None
    was_cached: bool
    validation_attempts: int
    validation_failed: bool


class LLMClient:
    def __init__(
        self,
        mode: str,
        cache_dir: Path,
        model: str = DEFAULT_MODEL,
        prompt_version: str = "v1",
        api_key: str | None = None,
        provider: str = "anthropic",
    ) -> None:
        if mode not in ("live", "cached", "disabled"):
            raise ValueError(f"unknown LLM mode: {mode}")
        if provider not in ("anthropic", "gemini"):
            raise ValueError(f"unknown LLM provider: {provider}")
        self.mode = mode
        self.model = model
        self.prompt_version = prompt_version
        self.api_key = api_key
        self.provider = provider
        self.cache = DiskCache(cache_dir)

    def complete(
        self, purpose: str, prompt: str, response_schema: type[T]
    ) -> tuple[T, LLMCallRecord]:
        if self.mode == "disabled":
            raise LLMDisabledError(f"LLM_MODE=disabled; cannot complete purpose={purpose!r}")

        key = prompt_hash(self.model, self.prompt_version, prompt)
        request_payload = {"purpose": purpose, "prompt": prompt}

        if self.mode == "cached":
            cached = self.cache.get(key)
            if cached is None:
                raise LLMCacheMissError(
                    f"No cached response for purpose={purpose!r} "
                    f"(model={self.model}, prompt_version={self.prompt_version}). "
                    "Run in live mode first to populate the cache, or handle this as a "
                    "graceful-degradation case."
                )
            parsed = response_schema.model_validate(cached["response"])
            record = LLMCallRecord(
                purpose=purpose, model=self.model, prompt_version=self.prompt_version,
                prompt_sha256=key, request_payload=request_payload, response_payload=cached["response"],
                input_tokens=cached.get("input_tokens"), output_tokens=cached.get("output_tokens"),
                cost_micros=0, latency_ms=0, was_cached=True,
                validation_attempts=1, validation_failed=False,
            )
            return parsed, record

        return self._complete_live(purpose, prompt, response_schema, key, request_payload)

    def _complete_live(
        self, purpose: str, prompt: str, response_schema: type[T], cache_key: str, request_payload: dict
    ) -> tuple[T, LLMCallRecord]:
        if not self.api_key:
            raise LLMDisabledError("LLM_MODE=live requires LLM_API_KEY")

        schema_json = json.dumps(response_schema.model_json_schema())
        system_prompt = (
            "Respond with a single JSON object matching this schema exactly, and nothing "
            f"else — no markdown fences, no preamble:\n{schema_json}"
        )

        attempts = 0
        last_error: Exception | None = None
        current_prompt = prompt
        t0 = time.monotonic()

        while attempts < MAX_VALIDATION_RETRIES:
            attempts += 1
            if self.provider == "gemini":
                response_text, usage = self._call_gemini(system_prompt, current_prompt)
            else:
                response_text, usage = self._call_anthropic(system_prompt, current_prompt)
            try:
                parsed_json = json.loads(response_text)
                parsed = response_schema.model_validate(parsed_json)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                current_prompt = (
                    f"{prompt}\n\nYour previous response was invalid: {exc}. "
                    "Respond again with ONLY a valid JSON object matching the schema."
                )
                continue

            latency_ms = int((time.monotonic() - t0) * 1000)
            self.cache.set(
                cache_key,
                {
                    "response": parsed_json,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                },
            )
            record = LLMCallRecord(
                purpose=purpose, model=self.model, prompt_version=self.prompt_version,
                prompt_sha256=cache_key, request_payload=request_payload, response_payload=parsed_json,
                input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                cost_micros=_estimate_cost_micros(usage), latency_ms=latency_ms, was_cached=False,
                validation_attempts=attempts, validation_failed=False,
            )
            return parsed, record

        raise LLMValidationError(
            f"purpose={purpose!r} failed schema validation after {attempts} attempts: {last_error}"
        )

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system_prompt,
            # The untrusted file content lives inside `user_prompt`'s own fenced
            # <untrusted_file_content> tags (see prompts/*.md) — never split across the
            # system/user boundary in a way that would let it masquerade as an instruction.
            "messages": [{"role": "user", "content": user_prompt}],
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(ANTHROPIC_API_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return text, usage

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        if not self.api_key:
            raise LLMDisabledError("LLM_MODE=live requires LLM_API_KEY")
        url = f"{GEMINI_API_URL}/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.post(url, params={"key": self.api_key}, json=body)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                raise LLMDisabledError("Gemini request was unavailable") from exc
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)
        usage_metadata = data.get("usageMetadata", {})
        usage = {
            "input_tokens": usage_metadata.get("promptTokenCount"),
            "output_tokens": usage_metadata.get("candidatesTokenCount"),
        }
        return text, usage


def _estimate_cost_micros(usage: dict) -> int:
    # Placeholder per-token pricing for cost tracking — a real deployment reads this from
    # the provider's published rate card, versioned the same way rate_card is (Schema §5.4).
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    micros_per_input_token = 3
    micros_per_output_token = 15
    return input_tokens * micros_per_input_token + output_tokens * micros_per_output_token
