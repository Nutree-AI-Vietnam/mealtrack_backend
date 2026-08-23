"""Pure helpers for stable serving-phrase identities."""

from __future__ import annotations

import unicodedata


def serving_phrase_key(text: str) -> str:
    """Return a stable lookup key for an external serving phrase."""
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    folded = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(folded.casefold().split())[:120]


__all__ = ["serving_phrase_key"]
