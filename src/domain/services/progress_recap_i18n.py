"""Resolve recap locale and look up fallback copy."""

from __future__ import annotations

from src.domain.constants.languages import resolve_app_locale
from src.domain.services.progress_recap_i18n_en_vi import EN, VI
from src.domain.services.progress_recap_i18n_es_fr_de import DE, ES, FR
from src.domain.services.progress_recap_i18n_ja_zh import JA, ZH

_TABLES = {"en": EN, "vi": VI, "es": ES, "fr": FR, "de": DE, "ja": JA, "zh": ZH}


def recap_locale(value: str | None) -> str:
    token = (value or "en").split(",")[0].split(";")[0].strip()
    return resolve_app_locale(token)


def recap_text(locale: str | None, key: str, **kwargs: object) -> str:
    table = _TABLES.get(recap_locale(locale), EN)
    return (table.get(key) or EN[key]).format(**kwargs)
