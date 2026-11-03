"""Saving a profile allergy only when the recipe data can check it."""

import pytest

from src.api.exceptions import ValidationException
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    _checked_allergies,
)

SEEDED = ("milk", "egg", "fish", "shellfish", "peanut", "tree_nut", "soy", "wheat")


class _Uow:
    def __init__(self, codes=SEEDED):
        self.catalog_recipes = self
        self._codes = codes

    async def list_allergen_codes(self):
        return list(self._codes)


@pytest.mark.asyncio
async def test_vietnamese_and_english_terms_are_saved_as_typed():
    saved = await _checked_allergies(_Uow(), ["tôm", "Đậu phộng", "milk"])

    # The user's own words are kept; the planner resolves them to codes.
    assert saved == ["tôm", "Đậu phộng", "milk"]


@pytest.mark.asyncio
async def test_blanks_and_repeats_are_dropped():
    assert await _checked_allergies(_Uow(), ["tôm", " ", "TÔM"]) == ["tôm"]


@pytest.mark.asyncio
async def test_an_empty_list_clears_the_allergies():
    assert await _checked_allergies(_Uow(), []) == []


@pytest.mark.asyncio
async def test_a_term_the_recipes_cannot_check_is_refused():
    with pytest.raises(ValidationException) as error:
        await _checked_allergies(_Uow(), ["tôm", "kiwi"])

    assert error.value.error_code == "ALLERGY_UNRESOLVED"
    assert "kiwi" in error.value.message


@pytest.mark.asyncio
async def test_a_code_the_catalog_does_not_seed_is_refused():
    # Nothing can be excluded for sesame when no recipe is linked to it.
    with pytest.raises(ValidationException):
        await _checked_allergies(_Uow(codes=("milk",)), ["mè"])
