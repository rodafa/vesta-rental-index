"""Load and cache the voice guide for Claude prompt construction."""

import os
from functools import lru_cache

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "voice_guide.md"
)


@lru_cache(maxsize=4)
def load_voice_guide(path: str = "") -> str:
    """Read a voice guide markdown file. Cached after first read per path.

    Args:
        path: Absolute path to the voice guide file. Defaults to the
              weekly-leasing guide for backward compatibility.
    """
    resolved = path or _DEFAULT_PATH
    try:
        with open(resolved, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
