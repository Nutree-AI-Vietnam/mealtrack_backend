"""PII guards for accuracy-baseline fixtures.

Committed fixtures may contain synthetic case ids only. Production meal ids,
user ids, image URLs, and contact data are rejected before a report is written.
"""

from __future__ import annotations

import re
from typing import Any

FORBIDDEN_KEYS = frozenset(
    {
        "address",
        "description",
        "dish_name",
        "dish_name_guess",
        "email",
        "exif",
        "firebase_uid",
        "gps",
        "image_id",
        "image_url",
        "imageid",
        "latitude",
        "longitude",
        "meal_id",
        "mealid",
        "phone",
        "phone_number",
        "raw_ai_response",
        "url",
        "user_id",
        "userid",
    }
)

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def assert_no_pii(value: Any, *, label: str = "document") -> None:
    """Reject identifiers and contact data anywhere in a fixture document."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS:
                raise ValueError(f"{label}: forbidden key {key}")
            assert_no_pii(item, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_pii(item, label=f"{label}[{index}]")
        return
    if isinstance(value, str):
        if _UUID_RE.search(value):
            raise ValueError(f"{label}: production-style id is not allowed")
        if _EMAIL_RE.search(value):
            raise ValueError(f"{label}: email address is not allowed")
        if _URL_RE.search(value):
            raise ValueError(f"{label}: URL is not allowed")
        return
    if value is None or isinstance(value, bool | int | float):
        return
    raise ValueError(f"{label}: unsupported fixture value")
