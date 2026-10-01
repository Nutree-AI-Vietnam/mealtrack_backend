"""Pure resolver for catalog display names by request language.

No DB access — operates only on already-loaded display projections
(``{"name": str, "name_vi": str | None}``).
"""

from typing import Any

from src.domain.constants.languages import normalize_language

_DIRECT_VI_GROCERY_NAMES = {
    "avocado": "Bơ",
    "banana": "Chuối",
    "bananas": "Chuối",
    "beef": "Thịt bò",
    "berries": "Các loại quả mọng",
    "broccoli": "Bông cải xanh",
    "carrot": "Cà rốt",
    "caramel": "Nước hàng",
    "chicken": "Thịt gà",
    "chicken breast": "Ức gà",
    "chickpeas": "Đậu gà",
    "chia": "Hạt chia",
    "coconut milk": "Nước cốt dừa",
    "coconut_milk": "Nước cốt dừa",
    "cucumber": "Dưa leo",
    "egg": "Trứng",
    "eggs": "Trứng",
    "fish sauce": "Nước mắm",
    "garlic": "Tỏi",
    "ginger": "Gừng",
    "greek yogurt": "Sữa chua Hy Lạp",
    "jasmine rice": "Gạo thơm",
    "lean beef": "Thịt bò nạc",
    "lemongrass": "Sả",
    "lime": "Chanh xanh",
    "mango": "Xoài",
    "milk": "Sữa",
    "mixed berries": "Các loại quả mọng",
    "oats": "Yến mạch",
    "oil": "Dầu ăn",
    "onion": "Hành tây",
    "oyster sauce": "Dầu hào",
    "oyster": "Dầu hào",
    "pork": "Thịt heo",
    "pork chops": "Sườn heo",
    "quinoa": "Hạt diêm mạch",
    "rice": "Gạo",
    "rolled oats": "Yến mạch cán dẹt",
    "salmon": "Cá hồi",
    "salmon fillet": "Phi lê cá hồi",
    "shallot": "Hành tím",
    "shallots": "Hành tím",
    "soy sauce": "Nước tương",
    "soy": "Nước tương",
    "spinach": "Rau chân vịt",
    "sugar": "Đường",
    "sweet potato": "Khoai lang",
    "tomato": "Cà chua",
    "tomatoes": "Cà chua",
    "tofu": "Đậu phụ",
    "whole grain bread": "Bánh mì nguyên cám",
    "whole_grain_bread": "Bánh mì nguyên cám",
    "yogurt": "Sữa chua",
}


def resolve_food_reference_display_name(
    projection: dict[str, Any],
    language: str | None,
) -> str:
    """Resolve one food-reference display name for the requested language.

    English (or an empty/unrecognized locale) always returns the catalog's
    canonical English name. Vietnamese uses ``name_vi`` when present, then
    falls back to English. A missing ``name_vi`` never triggers a live
    translate-on-read.
    """
    name = str(projection.get("name") or "")
    normalized = normalize_language(language)
    if normalized != "vi":
        return name

    name_vi = projection.get("name_vi")
    if name_vi:
        return str(name_vi)
    return name


def resolve_grocery_proposal_display_name(
    projection: dict[str, Any] | None,
    source_name: str,
    language: str | None,
) -> str:
    """Use authored catalog Vietnamese or direct labels for proposal groceries.

    This display-only fallback deliberately performs no translate-on-read. If
    there is no authored or known direct label, use a localized generic label
    rather than exposing English text or a numeric ingredient ID.
    """
    projection = projection or {}
    name = str(projection.get("name") or source_name).strip()
    source_name = source_name.strip()
    if name.isdecimal():
        name = source_name
    if normalize_language(language) != "vi":
        return name if name and not name.isdecimal() else "Ingredient"

    name_vi = str(projection.get("name_vi") or "").strip()
    if name_vi:
        return name_vi
    for candidate in (name, source_name):
        direct = _DIRECT_VI_GROCERY_NAMES.get(" ".join(candidate.casefold().split()))
        if direct:
            return direct
    return "Nguyên liệu"
