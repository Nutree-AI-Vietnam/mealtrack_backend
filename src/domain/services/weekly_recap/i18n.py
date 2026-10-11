"""Look up weekly recap copy, with singular forms and an English fallback."""

from __future__ import annotations

from src.domain.services.weekly_recap.strings import EN, TABLES

_ONE_ONLY = frozenset({"en", "es", "de"})
_ZERO_OR_ONE = frozenset({"fr"})


def text(locale: str, key: str, count: int | None = None, **params: object) -> str:
    """Return the copy for ``key`` in ``locale`` with ``params`` filled in.

    ``count`` picks the ``.one`` form when the locale treats it as singular;
    it does not fill ``{n}`` by itself. Missing keys fall back to English.
    """
    template = _template(TABLES.get(locale, EN), locale, key, count)
    if template is None:
        template = _template(EN, "en", key, count)
    if template is None:
        raise KeyError(key)
    return template.format(**params)


def is_singular(locale: str, count: int | None) -> bool:
    """Whether ``count`` takes the ``.one`` form in ``locale``."""
    if count is None:
        return False
    if locale in _ONE_ONLY:
        return count == 1
    if locale in _ZERO_OR_ONE:
        return count in (0, 1)
    return False


def _template(
    table: dict[str, str], locale: str, key: str, count: int | None
) -> str | None:
    if is_singular(locale, count) and f"{key}.one" in table:
        return table[f"{key}.one"]
    return table.get(key)
