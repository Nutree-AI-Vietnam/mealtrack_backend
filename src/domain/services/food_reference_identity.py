"""Pure identity-key helpers for provider-adopted food references.

Adopted platform rows use ``name_normalized = "{namespace}:{food_id}"``.
``normalize_food_name()`` strips ``:`` from typed ingredient text, so these
identity keys never collide with human seed names (seeds keep names like
``"beef"``; only FatSecret-adopted rows are identity-keyed today).
"""

from __future__ import annotations

FATSECRET_NAMESPACE = "fatsecret"
_SUPPORTED_PLATFORM_NAMESPACES = frozenset({FATSECRET_NAMESPACE})
_MAX_LOCALE_NAME_LENGTH = 255


def platform_name_normalized(namespace: str, food_id: str) -> str:
    """Build the identity key stored in ``name_normalized`` for adopted rows."""
    return f"{namespace}:{food_id}"


def platform_identity_prefix(namespace: str) -> str:
    """SQL ``LIKE`` prefix matching every identity key for one namespace."""
    return f"{namespace}:"


def is_supported_platform_namespace(namespace: str | None) -> bool:
    """True when adopt-style identity keys are defined for this namespace."""
    return namespace in _SUPPORTED_PLATFORM_NAMESPACES


def sanitize_locale_name(raw: object) -> str:
    """Strip control characters and cap length for a persisted display name."""
    cleaned = "".join(ch for ch in str(raw or "") if ch.isprintable())
    cleaned = " ".join(cleaned.split())
    return cleaned[:_MAX_LOCALE_NAME_LENGTH]
