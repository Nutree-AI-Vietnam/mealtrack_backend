"""The planner's serving rules are regional, not universal."""

from src.domain.services.weekly_meal_planner.meal_locale import (
    DEFAULT_LOCALE,
    VIETNAM,
    MealLocale,
    locale_by_code,
    locale_for_timezone,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    MAX_RICE_STEPS,
    MIN_RICE_STEPS,
    RICE_STEP_GRAMS,
    RICE_STEP_MACROS,
)


def test_the_vietnam_pack_still_carries_the_nin_numbers():
    """A cereal serving is 55 g of steamed rice, 20 g of carbohydrate."""
    assert VIETNAM.staple_step_grams == 55
    assert VIETNAM.staple_step_macros == (1.5, 20.0, 0.15)
    assert VIETNAM.staple_slots == frozenset({1, 2})


def test_the_composer_constants_come_from_the_default_pack():
    """One source of truth, so the two can never drift apart."""
    assert RICE_STEP_GRAMS == DEFAULT_LOCALE.staple_step_grams
    assert RICE_STEP_MACROS == DEFAULT_LOCALE.staple_step_macros
    assert MAX_RICE_STEPS == DEFAULT_LOCALE.max_staple_steps
    assert MIN_RICE_STEPS == DEFAULT_LOCALE.min_staple_steps


def test_a_vietnamese_timezone_plans_with_the_vietnam_pack():
    assert locale_for_timezone("Asia/Ho_Chi_Minh") is VIETNAM
    assert locale_for_timezone("asia/ho_chi_minh") is VIETNAM


def test_an_unknown_timezone_falls_back_rather_than_guessing():
    """Nothing about New York says how its users eat, so do not pretend."""
    assert locale_for_timezone("America/New_York") is DEFAULT_LOCALE
    assert locale_for_timezone(None) is DEFAULT_LOCALE
    assert locale_for_timezone("") is DEFAULT_LOCALE


def test_an_unregistered_code_resolves_to_nothing():
    assert locale_by_code("jp") is None
    assert locale_by_code(None) is None
    assert locale_by_code("vn") is VIETNAM


def test_a_dish_that_is_a_meal_on_its_own_takes_no_staple():
    """Pho is already dinner; it does not come with a bowl of rice."""
    assert VIETNAM.takes_staple("pho ga") is False
    assert VIETNAM.takes_staple("bun bo hue") is False
    assert VIETNAM.takes_staple("com tam") is False


def test_a_dish_cooked_the_savoury_way_takes_the_staple():
    assert VIETNAM.takes_staple("thit kho trung") is True
    assert VIETNAM.takes_staple("bo xao hanh tay") is True
    assert VIETNAM.takes_staple("ca kho to") is True


def test_a_region_can_eat_its_staple_at_every_meal():
    """The pack is the only thing that decides, so another region can differ."""
    everywhere = MealLocale(
        code="test",
        staple_name="Bread",
        staple_step_grams=40,
        staple_step_macros=(3.0, 20.0, 1.0),
        max_staple_steps=3,
        min_staple_steps=1,
        staple_slots=frozenset({0, 1, 2}),
        slot_calorie_shares=(0.3, 0.3, 0.4),
        staple_dish_words=("stew",),
        standalone_dish_words=("soup",),
        staple_item_words=("bread",),
    )

    assert 0 in everywhere.staple_slots
    assert everywhere.takes_staple("beef stew") is True
    assert everywhere.takes_staple("tomato soup") is False
