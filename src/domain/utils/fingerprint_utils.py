"""Pure hashing and idempotency fingerprint utilities."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonicalize_fingerprint(payload: Any) -> str:
    """SHA-256 of canonical JSON (sorted keys, compact separators)."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_idempotency_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > 160:
        raise ValueError("Idempotency-Key must be 160 characters or fewer")
    return key
