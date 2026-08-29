"""Supported translation locale policy."""

from __future__ import annotations

from collections.abc import Iterable

DEFAULT_LANGUAGE = "en"
SUPPORTED_TRANSLATION_LANGUAGES = frozenset({"en", "vi", "es", "fr", "de", "ja", "zh"})

# Locales exposed in the mobile language picker and used for notification copy.
ENABLED_APP_LOCALES = SUPPORTED_TRANSLATION_LANGUAGES


def normalize_language(language: str | None) -> str:
    """Normalize a language tag to its ISO-639-1 primary subtag."""
    if not language:
        return DEFAULT_LANGUAGE
    return (
        language.strip().lower().replace("_", "-").split("-", 1)[0] or DEFAULT_LANGUAGE
    )


def is_supported_language(language: str | None) -> bool:
    """Return whether a language belongs to the exact translation allowlist."""
    return normalize_language(language) in SUPPORTED_TRANSLATION_LANGUAGES


def is_supported_translation_pair(source: str | None, target: str | None) -> bool:
    """Return whether both source and target are supported translation locales."""
    return is_supported_language(source) and is_supported_language(target)


def supported_languages(values: Iterable[str]) -> set[str]:
    """Normalize and filter an iterable for callers that need an allowlist."""
    return {
        normalize_language(value) for value in values if is_supported_language(value)
    }
