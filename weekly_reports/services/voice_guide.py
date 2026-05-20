"""Load and cache the voice guide for Claude prompt construction."""

import os
from functools import lru_cache

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


@lru_cache(maxsize=1)
def load_voice_guide() -> str:
    """Read voice_guide.md from the data directory. Cached after first read."""
    path = os.path.join(_DATA_DIR, "voice_guide.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
