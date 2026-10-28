"""How people in one country usually eat, used to lead a weekly plan with home food."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

SUPPORTED_MEAL_REGIONS: tuple[str, ...] = ("VN", "US")


@dataclass(frozen=True)
class RegionalMealProfile:
    """Food culture for one region. Phrases are casefolded without accents."""

    region: str
    home_cuisines: tuple[str, ...]
    familiar_cuisines: tuple[str, ...]
    # Breakfast, lunch, dinner shares of the daily target.
    slot_calorie_shares: tuple[float, float, float]
    # Dish names that read as breakfast here, on top of the shared list.
    breakfast_forms: tuple[str, ...] = ()
    # Foods the shared list treats as a luxury but people here eat every week.
    everyday_foods: tuple[str, ...] = ()

    def cuisine_rank(self, cuisine: str | None) -> int:
        """Return 0 for home cuisine, 1 for a familiar one, 2 for anything else."""
        folded = fold_text(cuisine or "")
        if _mentions(folded, self.home_cuisines):
            return 0
        if _mentions(folded, self.familiar_cuisines):
            return 1
        return 2


_PROFILES = {
    "VN": RegionalMealProfile(
        region="VN",
        home_cuisines=("vietnamese", "vietnam", "viet nam"),
        familiar_cuisines=("korean", "japanese", "chinese", "thai"),
        # Lunch is a full rice meal, as large as dinner.
        slot_calorie_shares=(0.25, 0.375, 0.375),
        breakfast_forms=(
            "pho",
            "bun",
            "hu tieu",
            "mi quang",
            "banh canh",
            "com tam",
            "banh bao",
            "banh uot",
            "mien",
        ),
    ),
    "US": RegionalMealProfile(
        region="US",
        home_cuisines=(
            "american",
            "southern",
            "tex-mex",
            "cajun",
            "creole",
            "soul food",
            "hawaiian",
        ),
        familiar_cuisines=(
            "mexican",
            "italian",
            "mediterranean",
            "greek",
            "chinese",
            "japanese",
            "korean",
            "thai",
            "indian",
        ),
        # A light lunch and dinner as the main meal of the day.
        slot_calorie_shares=(0.25, 0.33, 0.42),
        breakfast_forms=(
            "breakfast",
            "egg",
            "eggs",
            "scrambled eggs",
            "frittata",
            "parfait",
            "granola",
            "oats",
            "overnight oats",
            "pancakes",
            "waffle",
            "waffles",
            "bagel",
            "english muffin",
            "hash",
            "grits",
            "cottage cheese",
        ),
        everyday_foods=("cheese", "salmon", "steak"),
    ),
}

_VN_TIMEZONES = frozenset({"Asia/Ho_Chi_Minh", "Asia/Saigon"})
_US_TIMEZONES = frozenset(
    {
        "America/New_York",
        "America/Detroit",
        "America/Chicago",
        "America/Menominee",
        "America/Denver",
        "America/Boise",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Anchorage",
        "America/Juneau",
        "America/Sitka",
        "America/Metlakatla",
        "America/Yakutat",
        "America/Nome",
        "America/Adak",
        "America/Indianapolis",
        "America/Fort_Wayne",
        "America/Louisville",
        "America/Knox_IN",
        "Pacific/Honolulu",
    }
)
_US_TIMEZONE_PREFIXES = (
    "US/",
    "America/Indiana/",
    "America/Kentucky/",
    "America/North_Dakota/",
)


def regional_meal_profile(region: str | None) -> RegionalMealProfile | None:
    """Return the food culture for a supported region code, else ``None``."""
    code = normalize_meal_region(region)
    return _PROFILES[code] if code is not None else None


def normalize_meal_region(value: object) -> str | None:
    """Return a supported upper-case region code, or ``None``."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if code in _PROFILES else None


def infer_meal_region(*, language: str | None, timezone: str | None) -> str | None:
    """Guess where someone's food habits come from when they have not said.

    A Vietnamese-speaking user eats Vietnamese food wherever they live, so
    language wins. Otherwise the device timezone tells VN from the US.
    """
    if language and language.strip().casefold().split("-")[0] == "vi":
        return "VN"
    zone = (timezone or "").strip()
    if zone in _VN_TIMEZONES:
        return "VN"
    if zone in _US_TIMEZONES or zone.startswith(_US_TIMEZONE_PREFIXES):
        return "US"
    return None


def fold_text(value: str) -> str:
    """Casefold and strip accents so Vietnamese and English compare alike."""
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )


def _mentions(folded: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in folded for phrase in phrases)
