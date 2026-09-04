#!/usr/bin/env python3
"""Pre-commit hook: reject `float(` in src/milaan/domain/ — the money path.

TRD C1 is the highest-severity constraint in the project. This is the cheap enforcement
that would otherwise silently invalidate the accuracy claim if a single float slipped in
during a late-night edit (Appflow §4.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

MONEY_PATH = Path("src/milaan/domain")
FORBIDDEN = "float("


def main() -> int:
    violations: list[str] = []
    for path in MONEY_PATH.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if FORBIDDEN in line:
                violations.append(f"{path}:{lineno}: {line.strip()}")
    if violations:
        print("float( found in the money path (domain/) — this is forbidden by TRD C1:")
        for v in violations:
            print(f"  {v}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
