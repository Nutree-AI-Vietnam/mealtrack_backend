"""Locale presentation helpers for meal and ingredient display names."""

from src.domain.constants.languages import normalize_language


def is_ascii_latin_label(name: str | None) -> bool:
    """Return whether a label is unaccented Latin catalog text."""
    stripped = (name or "").strip()
    if not stripped:
        return False
    return stripped.isascii() and any(character.isalpha() for character in stripped)


def already_in_target_language(name: str | None, language: str | None) -> bool:
    """Return whether a stored label is already localized for the locale."""
    target = normalize_language(language)
    if target == "en":
        return False
    stripped = (name or "").strip()
    return bool(stripped) and not is_ascii_latin_label(stripped)


def keep_stored_display_name(
    *,
    stored: str | None,
    translated: str | None,
    language: str | None,
) -> bool:
    """Keep a localized stored name instead of leftover English catalog text."""
    target = normalize_language(language)
    if target == "en":
        return False
    stored_name = (stored or "").strip()
    translated_name = (translated or "").strip()
    if not stored_name or not translated_name or stored_name == translated_name:
        return False
    return is_ascii_latin_label(translated_name) and not is_ascii_latin_label(
        stored_name
    )
