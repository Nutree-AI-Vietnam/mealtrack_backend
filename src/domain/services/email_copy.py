"""Localized lifecycle email copy for Jinja templates ({{ t.* }})."""

from __future__ import annotations

from src.domain.constants.languages import DEFAULT_LANGUAGE, normalize_language
from src.domain.services.email_copy_east_asian import EMAIL_COPY_EAST_ASIAN
from src.domain.services.email_copy_en_vi import EMAIL_COPY_EN_VI
from src.domain.services.email_copy_western import EMAIL_COPY_WESTERN

EMAIL_TEMPLATES = frozenset(
    {"welcome", "reengagement", "trial_expiring", "trial_cancelled"}
)

EMAIL_COPY: dict[str, dict[str, dict[str, str]]] = {
    **EMAIL_COPY_EN_VI,
    **EMAIL_COPY_WESTERN,
    **EMAIL_COPY_EAST_ASIAN,
}


def get_email_copy(language: str | None, template: str) -> dict[str, str]:
    """Return email strings for a template, falling back to English per key."""
    if template not in EMAIL_TEMPLATES:
        raise ValueError(f"Unknown email template: {template}")

    lang = normalize_language(language)
    en_copy = EMAIL_COPY[DEFAULT_LANGUAGE][template]
    locale_copy = EMAIL_COPY.get(lang, {}).get(template, {})
    return {**en_copy, **locale_copy}
