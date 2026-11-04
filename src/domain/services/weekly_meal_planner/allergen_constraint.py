"""Hard planner exclusion uses normalized allergen codes only.

Free-text allergen copy on a recipe is informational. Matching a code excludes
the recipe from planning. It is not a medical-grade safety guarantee.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_allergen_code(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def matched_allergen_codes(
    disclosures: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...]:
    """Map recipe disclosures to allergen reference codes.

    A disclosure links a code when it is the code itself ("shellfish") or when
    its text names an allergen in English or Vietnamese ("Peanuts",
    "Có cá và đậu nành"). Extra matches only exclude more recipes, so a broad
    text match fails safe.
    """

    catalog: dict[str, str] = {}
    for code in known_codes:
        normalized = normalize_allergen_code(str(code))
        if normalized:
            catalog.setdefault(normalized, str(code).strip())
    matched: list[str] = []
    seen: set[str] = set()

    def add(normalized: str) -> None:
        code = catalog.get(normalized)
        if code is None or code in seen:
            return
        seen.add(code)
        matched.append(code)

    for disclosure in disclosures:
        text = str(disclosure)
        exact = normalize_allergen_code(text)
        if exact in catalog:
            add(exact)
            continue
        for normalized in allergen_codes_in_text(text):
            add(normalized)
    return tuple(matched)


# Allergen words that name a seeded code. Keys are lower-case and keep
# Vietnamese diacritics, so "cá" (fish) never matches "cà" (eggplant) in recipe
# text. Users often type without diacritics; fold_allergen_text() handles that
# for preferences only (see _FOLDED_TERM_CODES).
_TERM_CODES: dict[str, frozenset[str]] = {
    term: frozenset(codes)
    for codes, terms in (
        (
            {"milk"},
            (
                "milk",
                "dairy",
                "lactose",
                "butter",
                "cheese",
                "cream",
                "whey",
                "casein",
                "yogurt",
                "yoghurt",
                "sữa",
                "sữa bò",
                "bơ sữa",
                "phô mai",
                "pho mát",
                "sữa chua",
                "kem sữa",
            ),
        ),
        ({"egg"}, ("egg", "eggs", "trứng", "trứng gà", "trứng vịt")),
        (
            {"fish"},
            (
                "fish",
                "fishes",
                "fish sauce",
                "anchovy",
                "anchovies",
                "tuna",
                "salmon",
                "mackerel",
                "dashi",
                "bonito",
                "katsuobushi",
                "cá",
                "nước mắm",
                "mắm",
                "mắm nêm",
                "mắm cá",
                "cá cơm",
            ),
        ),
        (
            {"shellfish"},
            (
                "shellfish",
                "shell fish",
                "crustacean",
                "crustaceans",
                "mollusk",
                "mollusks",
                "mollusc",
                "molluscs",
                "shrimp",
                "shrimps",
                "prawn",
                "prawns",
                "crab",
                "crabs",
                "lobster",
                "lobsters",
                "clam",
                "clams",
                "mussel",
                "mussels",
                "oyster",
                "oysters",
                "oyster sauce",
                "scallop",
                "scallops",
                "squid",
                "octopus",
                "tôm",
                "tép",
                "cua",
                "ghẹ",
                "ốc",
                "sò",
                "nghêu",
                "ngao",
                "hến",
                "hàu",
                "mực",
                "bạch tuộc",
                "mắm tôm",
                "ruốc",
                "dầu hào",
                "tôm khô",
            ),
        ),
        (
            {"peanut"},
            (
                "peanut",
                "peanuts",
                "peanut butter",
                "groundnut",
                "groundnuts",
                "đậu phộng",
                "lạc",
                "bơ đậu phộng",
            ),
        ),
        (
            {"tree_nut"},
            (
                "tree nut",
                "tree nuts",
                "tree_nut",
                "almond",
                "almonds",
                "cashew",
                "cashews",
                "hazelnut",
                "hazelnuts",
                "walnut",
                "walnuts",
                "pecan",
                "pecans",
                "pistachio",
                "pistachios",
                "macadamia",
                "hạt điều",
                "hạnh nhân",
                "óc chó",
                "hạt dẻ",
                "hạt mắc ca",
            ),
        ),
        ({"peanut", "tree_nut"}, ("nut", "nuts", "các loại hạt")),
        (
            {"soy"},
            (
                "soy",
                "soya",
                "soybean",
                "soybeans",
                "soy sauce",
                "tofu",
                "miso",
                "edamame",
                "đậu nành",
                "đậu hũ",
                "đậu phụ",
                "tàu hũ",
                "nước tương",
                "xì dầu",
                "tương",
                "chao",
            ),
        ),
        (
            {"wheat"},
            (
                "wheat",
                "wheat flour",
                "barley",
                "rye",
                "lúa mì",
                "bột mì",
                "mì căn",
            ),
        ),
        ({"fish", "shellfish"}, ("seafood", "hải sản", "đồ biển")),
        ({"wheat", "gluten"}, ("gluten", "gluten free")),
        (
            {"sesame"},
            (
                "sesame",
                "sesame seed",
                "sesame seeds",
                "sesame oil",
                "mè",
                "vừng",
                "dầu mè",
            ),
        ),
    )
    for term in terms
}


def fold_allergen_text(value: str) -> str:
    """Lower-case, map đ to d, drop diacritics and collapse spaces."""
    text = str(value).casefold().replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[_\-]+", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _accented_key(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value)).casefold()
    text = re.sub(r"[_\-]+", " ", text)
    return " ".join(text.split())


_FOLDED_TERM_CODES: dict[str, frozenset[str]] = {}
for _term, _codes in _TERM_CODES.items():
    _folded = fold_allergen_text(_term)
    _FOLDED_TERM_CODES[_folded] = _FOLDED_TERM_CODES.get(_folded, frozenset()) | _codes

_TEXT_PATTERN = re.compile(
    r"(?<!\w)("
    + "|".join(re.escape(term) for term in sorted(_TERM_CODES, key=len, reverse=True))
    + r")(?!\w)"
)


def allergen_codes_in_text(text: str) -> tuple[str, ...]:
    """Normalized codes for allergen words that appear in free text.

    Matching keeps Vietnamese diacritics and whole words only.
    """
    found: set[str] = set()
    for match in _TEXT_PATTERN.finditer(_accented_key(text)):
        found.update(_TERM_CODES[match.group(1)])
    return tuple(sorted(found))


def _term_codes(value: str) -> set[str]:
    cleaned = re.sub(
        r"\b(?:allerg(?:y|ies|ic)(?: to)?|dị ứng(?: với)?|di ung(?: voi)?)\b",
        " ",
        str(value),
        flags=re.IGNORECASE,
    )
    accented = _accented_key(cleaned)
    if accented in _TERM_CODES:
        return set(_TERM_CODES[accented])
    folded = fold_allergen_text(cleaned)
    if folded in _FOLDED_TERM_CODES:
        return set(_FOLDED_TERM_CODES[folded])
    normalized = normalize_allergen_code(folded)
    candidates = {normalized}
    if normalized.endswith("s") and not normalized.endswith("ss"):
        candidates.add(normalized[:-1])
    return candidates


def resolve_allergen_terms(
    preferences: Iterable[str], known_codes: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve each typed allergy to known codes.

    Returns (codes, unresolved terms). Callers must not plan around an
    unresolved term: it names an allergy the recipe data cannot check.
    """
    known: dict[str, str] = {}
    for raw_code in known_codes:
        code = str(raw_code).strip()
        normalized = normalize_allergen_code(code)
        if not normalized:
            continue
        existing = known.get(normalized)
        if existing is None or (code == normalized and existing != normalized):
            known[normalized] = code
    resolved: set[str] = set()
    unresolved: list[str] = []
    for value in preferences:
        text = str(value).strip()
        if not text or not fold_allergen_text(text):
            continue
        candidates = _term_codes(text)
        matches = {known[code] for code in candidates if code in known}
        if not matches:
            unresolved.append(text)
            continue
        resolved.update(matches)
    return tuple(sorted(resolved)), tuple(unresolved)


def resolve_allergen_preferences(
    preferences: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...] | None:
    """Map saved allergy text to known codes, or None if any term fails."""
    codes, unresolved = resolve_allergen_terms(preferences, known_codes)
    if unresolved:
        return None
    return codes


def recipe_excluded_by_allergen(
    recipe_codes: Iterable[str], user_allergies: Iterable[str]
) -> bool:
    recipe = {
        normalize_allergen_code(code) for code in recipe_codes if str(code).strip()
    }
    requested = {
        normalize_allergen_code(code) for code in user_allergies if str(code).strip()
    }
    if not requested:
        return False
    # Missing recipe disclosure data is unknown, not evidence of safety.
    if not recipe:
        return True
    return bool(recipe & requested)
