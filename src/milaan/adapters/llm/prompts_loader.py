"""Loads versioned prompt templates (Schema §5.4 `prompt_version` — every run records
which exact prompt text produced its LLM calls)."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt_template(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
