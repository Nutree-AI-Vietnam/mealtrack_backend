"""Cache keys for food search results."""

from __future__ import annotations

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")


def food_search_cache_key(query: str, language: str, *, mode: str, limit: int) -> str:
    """Stable key for one search shape.

    The cache layer truncates keys at 64 characters, so the query is hashed
    and the language trimmed; mode and limit keep autocomplete, full search
    and different page sizes from serving each other's entries.
    """
    normalized = _WHITESPACE.sub(" ", query.strip().lower())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"v4:{language.strip().lower()[:8]}:{mode}:{limit}:{digest}"
