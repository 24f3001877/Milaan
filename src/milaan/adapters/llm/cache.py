"""Content-addressed disk cache for LLM responses.

Keyed by sha256(model + prompt_version + prompt) so an identical prompt against the same
model/prompt-version always resolves to the same cache entry. This is what makes
`LLM_MODE=cached` deterministic and free (Appflow §4.2): CI never touches the network, and
the same input always produces the same output (C2).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def prompt_hash(model: str, prompt_version: str, prompt: str) -> str:
    joined = f"{model}|{prompt_version}|{prompt}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def set(self, key: str, value: dict) -> None:
        path = self._path(key)
        with path.open("w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write("\n")
