"""Allergy terms resolve in English and Vietnamese, and recipe text links codes."""

import pytest

from src.domain.services.weekly_meal_planner.allergen_constraint import (
    allergen_codes_in_text,
    matched_allergen_codes,
    resolve_allergen_preferences,
    resolve_allergen_terms,
)

SEEDED = (
    "milk",
    "egg",
    "fish",
    "shellfish",
    "peanut",
    "tree_nut",
    "soy",
    "wheat",
    "sesame",
)


@pytest.mark.parametrize(
    ("term", "codes"),
    [
        ("tôm", ("shellfish",)),
        ("tom", ("shellfish",)),
        ("hải sản", ("fish", "shellfish")),
        ("seafood", ("fish", "shellfish")),
        ("đậu phộng", ("peanut",)),
        ("dau phong", ("peanut",)),
        ("lạc", ("peanut",)),
        ("sữa", ("milk",)),
        ("trứng", ("egg",)),
        ("cá", ("fish",)),
        ("mè", ("sesame",)),
        ("đậu nành", ("soy",)),
        ("squid", ("shellfish",)),
        ("prawns", ("shellfish",)),
        ("soya", ("soy",)),
        ("sesame seeds", ("sesame",)),
        ("peanut butter", ("peanut",)),
        ("fish sauce", ("fish",)),
        ("gluten", ("wheat",)),
        ("nuts", ("peanut", "tree_nut")),
        ("allergic to peanuts", ("peanut",)),
        ("Dị ứng tôm", ("shellfish",)),
        ("Peanut allergy", ("peanut",)),
    ],
)
def test_terms_resolve_to_seeded_codes(term, codes):
    assert resolve_allergen_terms([term], SEEDED) == (codes, ())


def test_unknown_term_is_reported_not_dropped():
    codes, unresolved = resolve_allergen_terms(["tôm", "kiwi"], SEEDED)

    assert codes == ("shellfish",)
    assert unresolved == ("kiwi",)
    assert resolve_allergen_preferences(["tôm", "kiwi"], SEEDED) is None


def test_terms_only_resolve_to_codes_the_catalog_knows():
    assert resolve_allergen_terms(["gluten"], ("milk",)) == ((), ("gluten",))


def test_disclosure_text_links_every_named_allergen():
    assert matched_allergen_codes(["Peanuts", "Shellfish", "Tôm"], SEEDED) == (
        "peanut",
        "shellfish",
    )
    assert matched_allergen_codes(
        ["Có cá và đậu nành. Kiểm tra nhãn miso."], SEEDED
    ) == ("fish", "soy")
    assert matched_allergen_codes(
        ["Egg and wheat. Use gluten-free bread if needed."], SEEDED
    ) == ("egg", "wheat")


@pytest.mark.parametrize(
    "text",
    [
        "Cà ri gà, cà chua",  # cà (eggplant/tomato) is not cá (fish)
        "Coconut. Check the labels of curry paste.",
        "No major allergens listed. Check seasoning labels.",
        "Cháo trắng",  # cháo (porridge) is not chao (fermented tofu)
    ],
)
def test_text_scan_needs_whole_words_with_diacritics(text):
    assert allergen_codes_in_text(text) == ()
