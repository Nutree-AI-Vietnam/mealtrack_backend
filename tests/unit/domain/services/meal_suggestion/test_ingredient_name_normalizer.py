"""
Unit tests for ingredient_name_normalizer.normalize_food_name.
"""

import pytest

from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_identity,
    normalize_food_name,
)


@pytest.mark.unit
class TestNormalizeFoodName:
    """Tests for normalize_food_name — the single source of truth for name normalization."""

    # --- lowercase + strip ---

    def test_lowercases_input(self):
        assert normalize_food_name("SALMON") == "salmon"

    def test_strips_surrounding_whitespace(self):
        assert normalize_food_name("  oats  ") == "oats"

    def test_collapses_internal_whitespace(self):
        assert normalize_food_name("brown   rice") == "brown rice"

    # --- qualifier removal uses word boundaries (C1 regression suite) ---

    def test_strawberry_not_corrupted(self):
        """'raw' inside 'Strawberry' must NOT be removed (no word boundary)."""
        assert normalize_food_name("Strawberry") == "strawberry"

    def test_freshwater_fish_unchanged(self):
        """'fresh' inside 'freshwater' must NOT be removed (no word boundary)."""
        assert normalize_food_name("Freshwater fish") == "freshwater fish"

    def test_cream_cheese_unchanged(self):
        """No qualifier substring in 'cream cheese' — result unchanged."""
        assert normalize_food_name("Cream cheese") == "cream cheese"

    def test_cornbread_unchanged(self):
        """No qualifier substring in 'cornbread' — result unchanged."""
        assert normalize_food_name("Cornbread") == "cornbread"

    def test_chicken_breast_boneless_strips_qualifier_and_comma(self):
        """'Boneless' qualifier removed; trailing comma stripped."""
        result = normalize_food_name("Chicken Breast, Boneless")
        assert result == "chicken breast"

    def test_removes_raw_standalone(self):
        assert normalize_food_name("Raw chicken breast") == "chicken breast"

    def test_removes_multiple_qualifiers(self):
        result = normalize_food_name("large fresh organic chicken breast")
        assert result == "chicken breast"

    # --- other qualifier removals ---

    def test_removes_cooked(self):
        assert normalize_food_name("cooked rice") == "rice"

    def test_removes_fresh(self):
        assert normalize_food_name("fresh spinach") == "spinach"

    def test_removes_frozen(self):
        assert normalize_food_name("frozen peas") == "peas"

    def test_removes_grilled(self):
        assert normalize_food_name("grilled chicken") == "chicken"

    def test_removes_organic(self):
        assert normalize_food_name("organic carrots") == "carrots"

    def test_removes_sliced(self):
        assert normalize_food_name("sliced almonds") == "almonds"

    def test_removes_diced(self):
        assert normalize_food_name("diced tomatoes") == "tomatoes"

    def test_removes_minced(self):
        assert normalize_food_name("minced garlic") == "garlic"

    def test_removes_boneless(self):
        result = normalize_food_name("boneless chicken thigh")
        assert "boneless" not in result
        assert "chicken" in result

    def test_removes_skinless_and_boneless(self):
        result = normalize_food_name("skinless boneless chicken thigh")
        assert "skinless" not in result
        assert "boneless" not in result
        assert "chicken" in result
        assert "thigh" in result

    # --- punctuation stripped ---

    def test_comma_stripped(self):
        result = normalize_food_name("Chicken Breast, Boneless")
        assert "," not in result

    def test_parentheses_stripped(self):
        result = normalize_food_name("salmon (atlantic)")
        assert "(" not in result
        assert ")" not in result

    # --- convergence ---

    def test_raw_and_cooked_variants_converge(self):
        assert normalize_food_name("raw salmon") == normalize_food_name("cooked salmon")


@pytest.mark.unit
class TestNormalizeFoodIdentity:
    def test_preserves_nutrition_relevant_preparation_state(self):
        assert normalize_food_identity("raw rice") != normalize_food_identity(
            "cooked rice"
        )

    def test_matches_cosmetic_unicode_variants(self):
        assert normalize_food_identity("Ức gà") == normalize_food_identity("uc ga")

    def test_fresh_and_frozen_variants_converge(self):
        assert normalize_food_name("fresh peas") == normalize_food_name("frozen peas")

    # --- idempotent ---

    def test_idempotent_on_already_normalized(self):
        once = normalize_food_name("chicken breast")
        twice = normalize_food_name(once)
        assert once == twice

    # --- empty / edge cases ---

    def test_empty_string(self):
        assert normalize_food_name("") == ""

    def test_only_qualifier(self):
        # "raw" alone → empty string after removal
        assert normalize_food_name("raw") == ""

    def test_whitespace_only(self):
        assert normalize_food_name("   ") == ""
