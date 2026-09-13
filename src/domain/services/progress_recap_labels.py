"""Human dates for recap beats. Full weekday + month, never ISO or 2026-W36."""

from __future__ import annotations

from datetime import date

from src.domain.services.progress_recap_i18n import recap_locale, recap_text

_WEEKDAYS = {
    "en": "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split(),
    "vi": [
        "Thứ Hai",
        "Thứ Ba",
        "Thứ Tư",
        "Thứ Năm",
        "Thứ Sáu",
        "Thứ Bảy",
        "Chủ Nhật",
    ],
    "es": "lunes martes miércoles jueves viernes sábado domingo".split(),
    "fr": "lundi mardi mercredi jeudi vendredi samedi dimanche".split(),
    "de": "Montag Dienstag Mittwoch Donnerstag Freitag Samstag Sonntag".split(),
    "ja": "月 火 水 木 金 土 日".split(),
    "zh": "一 二 三 四 五 六 日".split(),
}
_MONTHS = {
    "en": (
        "January February March April May June "
        "July August September October November December"
    ).split(),
    "vi": [f"tháng {i}" for i in range(1, 13)],
    "es": (
        "enero febrero marzo abril mayo junio "
        "julio agosto septiembre octubre noviembre diciembre"
    ).split(),
    "fr": (
        "janvier février mars avril mai juin "
        "juillet août septembre octobre novembre décembre"
    ).split(),
    "de": (
        "Januar Februar März April Mai Juni "
        "Juli August September Oktober November Dezember"
    ).split(),
    "ja": "1月 2月 3月 4月 5月 6月 7月 8月 9月 10月 11月 12月".split(),
    "zh": "1月 2月 3月 4月 5月 6月 7月 8月 9月 10月 11月 12月".split(),
}


def friendly_day(value: str | None, locale: str) -> str:
    parsed = _parse_day(value)
    if parsed is None:
        return value or ""
    code = recap_locale(locale)
    weekday = _WEEKDAYS.get(code, _WEEKDAYS["en"])[parsed.weekday()]
    month = _MONTHS.get(code, _MONTHS["en"])[parsed.month - 1]
    if code in {"ja", "zh"}:
        return f"{month}{parsed.day}日 ({weekday})"
    return f"{weekday}, {parsed.day} {month}"


def friendly_week(value: str | None, locale: str) -> str:
    if not value or "-W" not in value:
        return value or ""
    year_s, week_s = value.split("-W", 1)
    try:
        monday = date.fromisocalendar(int(year_s), int(week_s), 1)
    except ValueError:
        return value
    return recap_text(locale, "week_of", label=friendly_day(monday.isoformat(), locale))


def friendly_month(value: str | None, locale: str) -> str:
    if not value or len(value) < 7:
        return value or ""
    try:
        year, month = int(value[:4]), int(value[5:7])
    except ValueError:
        return value
    code = recap_locale(locale)
    if code == "vi":
        return f"Tháng {month}, {year}"
    name = _MONTHS.get(code, _MONTHS["en"])[month - 1]
    if code in {"ja", "zh"}:
        return f"{year}{name}"
    return f"{name} {year}"


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
