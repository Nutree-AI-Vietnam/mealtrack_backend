"""Hard planner exclusion uses normalized allergen codes only.

Free-text allergen copy on a recipe is informational. Matching a code excludes
the recipe from planning. It is not a medical-grade safety guarantee.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_allergen_code(value: str) -> str:
    return fold_allergen_text(value).strip().replace("-", "_").replace(" ", "_")


def matched_allergen_codes(
    disclosures: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...]:
    """Keep disclosures that are the same code as an allergen reference row."""

    catalog: dict[str, str] = {}
    for code in known_codes:
        normalized = normalize_allergen_code(str(code))
        if normalized:
            catalog.setdefault(normalized, str(code).strip())
    matched: list[str] = []
    seen: set[str] = set()
    for disclosure in disclosures:
        matched_code = catalog.get(normalize_allergen_code(str(disclosure)))
        if matched_code is None or matched_code in seen:
            continue
        seen.add(matched_code)
        matched.append(matched_code)
    return tuple(matched)


_TREE_NUT_ALIASES = (
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
)

_TREE_NUT_CODES = {"tree_nut", "tree_nuts", *_TREE_NUT_ALIASES}

_ALLERGEN_ALIASES = {
    "nut": {
        "peanut",
        "peanuts",
        *_TREE_NUT_CODES,
    },
    "nuts": {
        "peanut",
        "peanuts",
        *_TREE_NUT_CODES,
    },
    **{alias: {"tree_nut", "tree_nuts", alias} for alias in _TREE_NUT_ALIASES},
    "dairy": {"milk", "dairy", "lactose"},
    "milk": {"milk", "dairy", "lactose"},
    "lactose": {"lactose"},
    "shellfish": {"shellfish", "crustacean", "mollusk", "shrimp", "crab", "lobster"},
    "crustacean": {"shellfish", "crustacean"},
    "mollusk": {"shellfish", "mollusk"},
    "shrimp": {"shellfish", "shrimp"},
    "prawn": {"shellfish", "crustacean", "shrimp"},
    "crab": {"shellfish", "crab"},
    "lobster": {"shellfish", "lobster"},
    "gluten": {"gluten", "wheat", "barley", "rye"},
    "dau_phong": {"peanut"},
    "lac": {"peanut"},
    "hanh_nhan": {"tree_nut", "almond"},
    "hat_dieu": {"tree_nut", "cashew"},
    "oc_cho": {"tree_nut", "walnut"},
    "hat_de_cuoi": {"tree_nut", "pistachio"},
    "dau_nanh": {"soy"},
    "dau_phu": {"soy"},
    "sua": {"milk", "dairy"},
    "tom": {"shellfish", "shrimp"},
    "cua": {"shellfish", "crab"},
    "ca": {"fish"},
    "trung": {"egg"},
    "me": {"sesame"},
    "vung": {"sesame"},
    "celery": {"celery"},
    "mustard": {"mustard"},
    "lupin": {"lupin"},
    "sulfite": {"sulfite"},
    "sulphite": {"sulfite"},
}

# Ingredient wording used by free-text recipe sources (for example generated
# recipes). Catalog recipes continue to use their reviewed allergen codes.
_ALLERGEN_INGREDIENT_TERMS: dict[str, frozenset[str]] = {
    "peanut": frozenset(
        {"peanut", "peanuts", "groundnut", "groundnuts", "dau phong", "lac"}
    ),
    "tree_nut": frozenset(
        {
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
            "brazil nut",
            "hanh nhan",
            "hat dieu",
            "oc cho",
            "hat de cuoi",
        }
    ),
    "milk": frozenset(
        {
            "milk",
            "milk powder",
            "powdered milk",
            "condensed milk",
            "evaporated milk",
            "buttermilk",
            "dairy",
            "whey",
            "casein",
            "lactalbumin",
            "cheese",
            "yogurt",
            "yoghurt",
            "curd",
            "butter",
            "cream",
            "ghee",
            "ice cream",
            "sua",
            "sua chua",
            "pho mai",
            "vang sua",
        }
    ),
    "dairy": frozenset(
        {
            "milk",
            "milk powder",
            "powdered milk",
            "condensed milk",
            "evaporated milk",
            "buttermilk",
            "dairy",
            "whey",
            "casein",
            "lactalbumin",
            "cheese",
            "yogurt",
            "yoghurt",
            "curd",
            "butter",
            "cream",
            "ghee",
            "ice cream",
            "sua",
            "sua chua",
            "pho mai",
            "vang sua",
        }
    ),
    "lactose": frozenset(
        {
            "lactose",
            "milk",
            "whey",
            "yogurt",
            "yoghurt",
            "curd",
            "cream",
            "ice cream",
            "condensed milk",
            "milk powder",
            "sua",
            "sua chua",
            "sua dac",
            "bot sua",
        }
    ),
    "shellfish": frozenset(
        {
            "shellfish",
            "crustacean",
            "mollusk",
            "mollusc",
            "shrimp",
            "prawn",
            "crab",
            "lobster",
            "crayfish",
            "clam",
            "oyster",
            "mussel",
            "scallop",
            "squid",
            "octopus",
            "shrimp paste",
            "oyster sauce",
            "tom",
            "cua",
            "ngheu",
            "so",
            "hau",
            "vet",
            "muc",
            "bach tuoc",
        }
    ),
    "crustacean": frozenset(
        {"crustacean", "shrimp", "prawn", "crab", "lobster", "crayfish"}
    ),
    "mollusk": frozenset(
        {
            "mollusk",
            "mollusc",
            "clam",
            "oyster",
            "mussel",
            "scallop",
            "squid",
            "octopus",
        }
    ),
    "soy": frozenset(
        {"soy", "soya", "tofu", "edamame", "miso", "tempeh", "dau nanh", "dau phu"}
    ),
    "egg": frozenset({"egg", "eggs", "albumin", "mayonnaise", "trung"}),
    "fish": frozenset({"fish", "anchovy", "anchovies", "cod", "salmon", "tuna", "ca"}),
    "sesame": frozenset({"sesame", "tahini", "vung", "me"}),
    "wheat": frozenset({"wheat", "spelt", "semolina", "farina", "lua mi"}),
    "gluten": frozenset(
        {"gluten", "wheat", "barley", "rye", "spelt", "malt", "lua mi", "lua mach"}
    ),
    "celery": frozenset({"celery", "celeriac"}),
    "mustard": frozenset({"mustard", "dijon"}),
    "lupin": frozenset({"lupin"}),
    "sulfite": frozenset({"sulfite", "sulphite", "sulfur dioxide", "sulphur dioxide"}),
}


def fold_allergen_text(value: str) -> str:
    """Case-fold and remove accents so reviewed Vietnamese terms match."""
    folded = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    return "".join(char for char in folded if not unicodedata.combining(char))


def _clean_allergen_preference(preference: str) -> str:
    cleaned = fold_allergen_text(str(preference))
    cleaned = re.sub(
        r"^\s*(?:(?:di\s+ung)|(?:allergy(?:\s+to)?)|(?:allergic(?:\s+to)?))\s+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\s+(?:allergy|allergic)$", "", cleaned)
    cleaned = re.sub(
        r"^\s*(?:(?:to|of)|(?:intolerance\s+to)|(?:intolerant\s+to))\s+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\s+(?:intolerance|intolerant)$", "", cleaned)
    return cleaned


def allergen_codes(preference: str) -> frozenset[str]:
    """Resolve a saved preference label to normalized allergen codes."""
    normalized = normalize_allergen_code(_clean_allergen_preference(preference))
    singular = (
        normalized[:-1]
        if normalized.endswith("s") and not normalized.endswith("ss")
        else normalized
    )
    aliases = _ALLERGEN_ALIASES.get(normalized) or _ALLERGEN_ALIASES.get(singular)
    codes = set(aliases or {normalized, singular})
    if singular != normalized:
        codes.add(singular)
    return frozenset(code for code in codes if code)


def allergen_terms(preference: str) -> frozenset[str]:
    """Resolve a saved allergy label to conservative free-text ingredient terms."""
    normalized = normalize_allergen_code(_clean_allergen_preference(preference))
    # Keep milk allergy distinct from lactose intolerance in free-text scans,
    # while planner catalog aliases can still match older lactose disclosures.
    codes = (
        {"milk", "dairy"}
        if normalized in {"milk", "dairy"}
        else allergen_codes(preference)
    )
    terms: set[str] = set()
    for code in codes:
        terms.update(_ALLERGEN_INGREDIENT_TERMS.get(code, ()))
    return frozenset(terms)


def resolve_allergen_preferences(
    preferences: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...] | None:
    """Map common saved preference aliases to known codes or fail closed."""
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
    for value in preferences:
        candidates = set(allergen_codes(value))
        if not candidates:
            continue
        normalized = normalize_allergen_code(_clean_allergen_preference(value))
        if not normalized:
            continue
        matches = {known[code] for code in candidates if code in known}
        if not matches:
            return None
        resolved.update(matches)
    return tuple(sorted(resolved))


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
