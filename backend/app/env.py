"""Minimal .env loader.

Avoids a dependency for one job. Values already present in the real environment
always win, so a shell export still overrides the file.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load(path: Path | None = None) -> list[str]:
    """Load KEY=VALUE lines into os.environ. Returns the names that were set."""
    target = path or ENV_FILE
    if not target.exists():
        return []
    loaded: list[str] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.getenv(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded
