"""Every recap copy table has English's keys and placeholders."""

from __future__ import annotations

from string import Formatter

import pytest

from src.domain.services.weekly_recap.strings import EN, TABLES

ONE = ".one"
# Languages with a singular form; the others never pick ``.one`` keys.
SINGULAR_LOCALES = frozenset({"en", "es", "fr", "de"})


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def _base_keys(table: dict[str, str]) -> set[str]:
    return {key for key in table if not key.endswith(ONE)}


def test_tables_cover_the_app_locales() -> None:
    assert sorted(TABLES) == ["de", "en", "es", "fr", "ja", "vi", "zh"]


@pytest.mark.parametrize("locale", sorted(TABLES))
def test_keys_match_english(locale: str) -> None:
    assert _base_keys(TABLES[locale]) == _base_keys(EN)


@pytest.mark.parametrize("locale", sorted(TABLES))
def test_placeholders_match_english(locale: str) -> None:
    for key, template in TABLES[locale].items():
        assert template.strip(), key
        if key.endswith(ONE):
            base = key[: -len(ONE)]
            # A singular form may spell the number out ("Un logro").
            assert _fields(template) <= _fields(EN[base]), key
        else:
            assert _fields(template) == _fields(EN[key]), key


@pytest.mark.parametrize("locale", sorted(TABLES))
def test_singular_forms_only_where_the_language_has_them(locale: str) -> None:
    singular = set(TABLES[locale]) - _base_keys(TABLES[locale])

    if locale in SINGULAR_LOCALES:
        assert singular == set(EN) - _base_keys(EN)
    else:
        assert singular == set()
