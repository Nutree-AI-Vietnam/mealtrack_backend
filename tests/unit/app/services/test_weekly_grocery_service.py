from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.api.schemas.response.weekly_meal_planner_responses import GroceryItemResponse
from src.app.services.weekly_grocery_service import (
    WeeklyGroceryService,
    _resolve_grocery_category,
    _slot_from_meal,
)
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)
from src.domain.services.weekly_meal_planner.grocery_projection import (
    SYNTHETIC_INGREDIENT_ID_OFFSET,
    GroceryIngredient,
    GroceryInteraction,
    GrocerySlot,
    PantryAvailability,
    aggregate_grocery,
    apply_recipe_override,
    deterministic_ingredient_id,
)


def _plan():
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=2,
        preferences=WeeklyMealPlanPreferences(people=2),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{slot}",
                day_index=day,
                slot_index=slot,
                recipe_id="recipe-1" if day == 0 and slot == 0 else None,
            )
            for day in range(7)
            for slot in range(3)
        ),
    )


class _Catalog:
    async def get_meals(self, recipe_ids):
        return [await self.get_meal(recipe_id) for recipe_id in recipe_ids]

    async def get_meal(self, recipe_id):
        return CatalogMeal(
            id=recipe_id,
            catalog_key=recipe_id,
            content_hash="a" * 64,
            name="Tomato",
            cuisine="vietnamese",
            description=None,
            image_url=None,
            protein_g=Decimal("10"),
            carbs_g=Decimal("20"),
            fat_g=Decimal("3"),
            fiber_g=Decimal("2"),
            base_servings=1,
            serving_confidence="verified",
            meal_types=("lunch",),
            ingredients=(
                CatalogMealIngredient(
                    food_reference_id=7,
                    display_name="Tomato",
                    quantity=Decimal("150"),
                    unit="g",
                    category="produce",
                ),
            ),
        )


class _Plans:
    async def list_pantry(self, **kwargs):
        return [
            {
                "food_reference_id": 7,
                "available_amount": Decimal("100"),
                "available_unit": "g",
            }
        ]

    async def list_grocery_interactions(self, **kwargs):
        return []


class _Uow:
    catalog_recipes = _Catalog()
    weekly_meal_plans = _Plans()


@pytest.mark.asyncio
async def test_authoritative_batch_miss_does_not_fall_back_to_per_recipe_reads():
    class MissingCatalog(_Catalog):
        async def get_meals(self, recipe_ids):
            self.requested_ids = set(recipe_ids)
            return []

        async def get_meal(self, recipe_id):
            pytest.fail("A missing batch result must not trigger a per-ID retry")

    catalog = MissingCatalog()
    plan = _plan()
    plan = replace(
        plan, slots=tuple(replace(slot, recipe_id="deleted") for slot in plan.slots)
    )
    uow = type("Uow", (), {"catalog_recipes": catalog, "weekly_meal_plans": _Plans()})()
    assert await WeeklyGroceryService().calculate(uow, plan) == ()
    assert catalog.requested_ids == {"deleted"}


@pytest.mark.asyncio
async def test_groceries_scale_people_and_subtract_pantry():
    categories = await WeeklyGroceryService().calculate(_Uow(), _plan())

    item = categories[0].items[0]
    assert item.category == "fresh_produce"
    assert item.total_needed == 300
    assert item.stock_amount == 100
    assert item.status == "need_more"
    assert item.quantity_confidence == "verified"


@pytest.mark.asyncio
async def test_groceries_do_not_silently_scale_unknown_servings():
    plan = _plan()

    class CatalogWithoutServing(_Catalog):
        async def get_meal(self, recipe_id):
            meal = await super().get_meal(recipe_id)
            return meal.__class__(
                **{
                    **meal.__dict__,
                    "base_servings": 1,
                    "serving_confidence": "unknown",
                }
            )

    class UowWithoutServing:
        catalog_recipes = CatalogWithoutServing()
        weekly_meal_plans = _Plans()

    categories = await WeeklyGroceryService().calculate(UowWithoutServing(), plan)

    item = categories[0].items[0]
    assert item.total_needed == 150
    assert item.quantity_confidence == "unscaled"


@pytest.mark.asyncio
async def test_groceries_allocate_partial_stock_once_across_days_and_keep_kind():
    plan = _plan()
    plan = WeeklyMealPlan(
        **{
            **plan.__dict__,
            "people": 1,
            "preferences": WeeklyMealPlanPreferences(people=1),
            "slots": tuple(
                WeeklyMealPlanSlot(
                    id=slot.id,
                    day_index=slot.day_index,
                    slot_index=slot.slot_index,
                    recipe_id=(
                        "recipe-1"
                        if slot.day_index in (0, 1) and slot.slot_index == 0
                        else None
                    ),
                )
                for slot in plan.slots
            ),
        }
    )

    class PlansWithBoughtStock(_Plans):
        async def list_pantry(self, **kwargs):
            return [
                {
                    "food_reference_id": 7,
                    "available_amount": Decimal("50"),
                    "available_unit": "g",
                }
            ]

        async def list_grocery_interactions(self, **kwargs):
            return [{"food_reference_id": 7, "checked": True}]

    class UowWithBoughtStock:
        catalog_recipes = _Catalog()
        weekly_meal_plans = PlansWithBoughtStock()

    item = (await WeeklyGroceryService().calculate(UowWithBoughtStock(), plan))[
        0
    ].items[0]

    assert item.stock_amount == 50
    assert item.stock_kind == "bought"
    assert item.status == "need_more"
    assert item.remaining == 250
    assert item.daily_amounts == (
        {"day_index": 0, "total_needed": 150, "remaining": 100},
        {"day_index": 1, "total_needed": 150, "remaining": 150},
    )
    response = GroceryItemResponse(**item.__dict__)
    assert response.remaining == 250
    assert response.stock_kind == "bought"
    assert [day.model_dump() for day in response.daily_amounts] == list(
        item.daily_amounts
    )

    class PlansWithOwnedStock(PlansWithBoughtStock):
        async def list_grocery_interactions(self, **kwargs):
            return [{"food_reference_id": 7, "manually_owned": True}]

    class UowWithOwnedStock:
        catalog_recipes = _Catalog()
        weekly_meal_plans = PlansWithOwnedStock()

    owned_item = (await WeeklyGroceryService().calculate(UowWithOwnedStock(), plan))[
        0
    ].items[0]
    assert owned_item.stock_kind == "owned"


def test_quantity_normalization_only_uses_exact_conversions():
    assert normalize_ingredient_quantity(1, "kg").amount == Decimal("1000")
    assert normalize_ingredient_quantity(250, "g").unit == "g"
    assert normalize_ingredient_quantity(2, "pieces").dimension == "count:piece"
    count = normalize_ingredient_quantity(2, "tomatoes")
    assert count.dimension == "raw:tomatoes"
    assert count.confidence == "unknown"


def test_eggplant_is_produce_while_eggs_are_protein():
    assert _resolve_grocery_category("Eggplant", None) == "fresh_produce"
    assert _resolve_grocery_category("2 eggs", None) == "protein"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("algorithm_version", "expected_days"),
    [("v3-compact", {0: 600}), ("v2", {0: 300, 1: 300})],
)
async def test_a_compact_week_buys_the_leftover_lunch_with_its_dinner(
    algorithm_version, expected_days
):
    dinner_then_lunch = {(0, 2), (1, 1)}
    plan = replace(
        _plan(),
        algorithm_version=algorithm_version,
        slots=tuple(
            replace(
                slot,
                recipe_id="recipe-1"
                if (slot.day_index, slot.slot_index) in dinner_then_lunch
                else None,
            )
            for slot in _plan().slots
        ),
    )

    categories = await WeeklyGroceryService().calculate(_Uow(), plan)

    item = categories[0].items[0]
    assert item.total_needed == 600
    assert {
        day["day_index"]: day["total_needed"] for day in item.daily_amounts
    } == expected_days


def test_seasonings_are_staples_whatever_the_catalog_category():
    assert _resolve_grocery_category("Nước mắm", "pantry") == "staples"
    assert _resolve_grocery_category("Tỏi băm", "produce") == "staples"
    assert _resolve_grocery_category("Ớt chuông", "produce") == "fresh_produce"


@pytest.mark.asyncio
async def test_staples_follow_the_food_to_buy():
    class CatalogWithSalt(_Catalog):
        async def get_meal(self, recipe_id):
            meal = await super().get_meal(recipe_id)
            salt = CatalogMealIngredient(
                food_reference_id=None,
                display_name="Muối",
                quantity=Decimal("5"),
                unit="g",
            )
            return replace(meal, ingredients=(salt, *meal.ingredients))

    uow = type(
        "Uow", (), {"catalog_recipes": CatalogWithSalt(), "weekly_meal_plans": _Plans()}
    )()

    categories = await WeeklyGroceryService().calculate(uow, _plan())

    assert [category.category for category in categories] == [
        "fresh_produce",
        "staples",
    ]
    assert [item.name for item in categories[-1].items] == ["Muối"]


@pytest.mark.asyncio
async def test_groceries_exclude_vietnamese_equipment_from_catalog_and_payload():
    class CatalogWithEquipment(_Catalog):
        async def get_meal(self, recipe_id):
            meal = await super().get_meal(recipe_id)
            return replace(
                meal,
                ingredients=(
                    *meal.ingredients,
                    CatalogMealIngredient(
                        food_reference_id=8,
                        display_name="Tô",
                        quantity=Decimal("1"),
                        unit="piece",
                    ),
                    CatalogMealIngredient(
                        food_reference_id=9,
                        display_name="Dụng cụ",
                        quantity=Decimal("20"),
                        unit="g",
                    ),
                ),
                recipe_payload={
                    "ingredients": [
                        {"name": "Dụng cụ sơ chế", "quantity": 15, "unit": "g"},
                        {
                            "name": "Olive oil",
                            "quantity": 10,
                            "unit": "g",
                        },
                        {
                            "name": "Whisk",
                            "quantity": 1,
                            "unit": "piece",
                            "is_equipment": True,
                        },
                    ]
                },
            )

    meal = await CatalogWithEquipment().get_meal("recipe-1")
    grocery_service = WeeklyGroceryService()

    class UowWithEquipment:
        catalog_recipes = CatalogWithEquipment()
        weekly_meal_plans = _Plans()

    categories = await grocery_service.calculate(UowWithEquipment(), _plan())
    calculated_names = {item.name for category in categories for item in category.items}
    proposal_items = grocery_service.project(_plan(), [meal])
    slot_names = {
        ingredient.name
        for ingredient in _slot_from_meal(
            meal, plan_people=2, override=None
        ).ingredients
    }

    assert calculated_names == {"Tomato", "Olive oil"}
    assert {item.name for item in proposal_items} == {"Tomato", "Olive oil"}
    assert {item.ingredient_id for item in proposal_items} == {
        7,
        deterministic_ingredient_id("Olive oil"),
    }
    assert slot_names == {"Tomato", "Olive oil"}


@pytest.mark.asyncio
async def test_unmapped_ingredients_are_not_doubled_in_groceries():
    meal = CatalogMeal(
        id="recipe-1",
        catalog_key="recipe-1",
        content_hash="f" * 64,
        name="Phi lê cá điêu hồng",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("60"),
        carbs_g=Decimal("10"),
        fat_g=Decimal("15"),
        fiber_g=Decimal("1"),
        base_servings=1,
        serving_confidence="verified",
        meal_types=("lunch", "dinner"),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=None,
                display_name="Phi lê cá điêu hồng",
                quantity=Decimal("300"),
                unit="g",
                category="protein",
            ),
        ),
        recipe_payload={
            "ingredients": [
                {
                    "name": "Phi lê cá điêu hồng",
                    "quantity": 300,
                    "unit": "g",
                    "food_reference_id": None,
                }
            ]
        },
    )

    slot = _slot_from_meal(meal, plan_people=1, override=None)
    assert len(slot.ingredients) == 1
    assert slot.ingredients[0].name == "Phi lê cá điêu hồng"
    assert slot.ingredients[0].quantity == Decimal("300")

    grocery_service = WeeklyGroceryService()
    items = grocery_service.project(_plan(), [meal])
    fish_item = next(item for item in items if item.name == "Phi lê cá điêu hồng")
    # For 2 people in _plan() with base_servings=1, total needed should be 2 * 300 = 600g (NOT 1200g doubled)
    assert fish_item.total_needed == 600.0


def test_deterministic_ingredient_id_is_namespaced_to_prevent_collision():
    names = [
        "Water",
        "Salt",
        "Phi lê cá điêu hồng",
        "Rau muống",
        "Olive oil",
    ]
    for name in names:
        val = deterministic_ingredient_id(name)
        assert val >= SYNTHETIC_INGREDIENT_ID_OFFSET
        assert val <= 2_147_483_647


def test_deterministic_ingredient_id_zero_and_one_modulo_do_not_collide(monkeypatch):
    import src.domain.services.weekly_meal_planner.grocery_projection as gp

    monkeypatch.setattr(
        gp.zlib,
        "crc32",
        lambda b: 1_000_000_000 if b == b"ing_zero" else 1_000_000_001,
    )

    id_zero = deterministic_ingredient_id("ing_zero")
    id_one = deterministic_ingredient_id("ing_one")

    assert id_zero == SYNTHETIC_INGREDIENT_ID_OFFSET
    assert id_one == SYNTHETIC_INGREDIENT_ID_OFFSET + 1
    assert id_zero != id_one

    monkeypatch.undo()
    assert deterministic_ingredient_id("") == SYNTHETIC_INGREDIENT_ID_OFFSET


def test_unmapped_ingredient_does_not_collide_with_low_food_reference_id_pantry():
    unmapped_line = GroceryIngredient(
        position=0,
        food_reference_id=None,
        name="Special Herb",
        quantity=Decimal("50"),
        unit="g",
        category="produce",
    )
    slot = GrocerySlot(
        recipe_id="r1",
        publication_status="published",
        nutrition_status="ready",
        is_active=True,
        base_servings=1,
        serving_confidence="verified",
        people=1,
        ingredients=(unmapped_line,),
    )
    pantry = [
        PantryAvailability(
            food_reference_id=7,
            available_amount=Decimal("100"),
            available_unit="g",
        )
    ]
    interactions = [
        GroceryInteraction(
            food_reference_id=7,
            checked=True,
            do_not_buy=True,
        )
    ]
    items = aggregate_grocery([slot], pantry, interactions)
    assert len(items) == 1
    herb_item = items[0]
    assert herb_item.name == "Special Herb"
    assert herb_item.ingredient_id >= SYNTHETIC_INGREDIENT_ID_OFFSET
    assert herb_item.available_amount is None
    assert herb_item.checked is False
    assert herb_item.do_not_buy is False
    assert herb_item.status == "needed"


def test_slot_from_meal_non_contiguous_positions_does_not_collide_and_override_works_correctly():
    meal = CatalogMeal(
        id="recipe-non-contiguous",
        catalog_key="recipe-non-contiguous",
        content_hash="c" * 64,
        name="Chicken Bowl",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("40"),
        carbs_g=Decimal("50"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("2"),
        base_servings=1,
        serving_confidence="verified",
        meal_types=("lunch", "dinner"),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=7,
                display_name="Rice",
                quantity=Decimal("100"),
                unit="g",
                category="pantry",
                position=1,
            ),
            CatalogMealIngredient(
                food_reference_id=8,
                display_name="Chicken",
                quantity=Decimal("200"),
                unit="g",
                category="protein",
                position=3,  # non-contiguous gap at 2
            ),
        ),
        recipe_payload={
            "ingredients": [
                {
                    "name": "Special Sauce",
                    "quantity": 50,
                    "unit": "g",
                    "food_reference_id": None,
                },
                {
                    "name": "Garnish Herb",
                    "quantity": 10,
                    "unit": "g",
                    "food_reference_id": None,
                },
                {
                    # duplicate payload entry with same name should be skipped
                    "name": "Special Sauce",
                    "quantity": 50,
                    "unit": "g",
                    "food_reference_id": None,
                },
            ]
        },
    )

    slot = _slot_from_meal(meal, plan_people=1, override=None)
    assert len(slot.ingredients) == 4
    positions = [item.position for item in slot.ingredients]
    assert len(set(positions)) == 4
    assert positions == [1, 3, 4, 5]

    sauce = next(item for item in slot.ingredients if item.name == "Special Sauce")
    assert sauce.position == 4  # Does not collide with position 3!

    # Now verify that applying an override for position 3 removes ONLY Chicken, not Special Sauce
    override = {
        "removed_ingredients": [{"position": 3}],
        "ingredient_changes": [],
    }
    slot_with_override = _slot_from_meal(meal, plan_people=1, override=override)
    remaining_after_override = apply_recipe_override(
        slot_with_override.ingredients, slot_with_override.recipe_override
    )
    remaining_names = [item.name for item in remaining_after_override]
    assert remaining_names == ["Rice", "Special Sauce", "Garnish Herb"]
    assert "Chicken" not in remaining_names


@pytest.mark.asyncio
async def test_skipped_slots_buy_no_groceries():
    plan = _plan()
    skipped = replace(
        plan,
        slots=tuple(
            replace(slot, is_skipped=True) if slot.recipe_id else slot
            for slot in plan.slots
        ),
    )
    service = WeeklyGroceryService()

    planned = await service.calculate(_Uow(), plan)
    after_skip = await service.calculate(_Uow(), skipped)

    assert [item.ingredient_id for c in planned for item in c.items] == [7]
    assert after_skip == ()
    assert service.project(skipped, [await _Catalog().get_meal("recipe-1")]) == ()


def _saved_recipe(**overrides):
    from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot

    data = {
        "meal_name": "Tomato eggs",
        "macros": {"protein": 12, "carbs": 8, "fat": 9},
        "servings": 4,
        "ingredients": [
            # Same food as the catalog recipe's tomato: one grocery line.
            {"name": "Tomato", "amount": 400, "unit": "g", "food_reference_id": 7},
            {"name": "Eggs", "amount": 8, "unit": ""},
            {"name": "Fish sauce", "amount": 0, "unit": "", "grams": 20},
            {"name": "Salt", "amount": 0, "unit": ""},
        ],
        "recipe_import": {"servings_estimated": False},
    }
    data.update(overrides)
    return SavedRecipeSnapshot.from_saved_suggestion("recipe_import_imp-9", data)


def _plan_with_saved(*, skipped=False, recipe=None):
    plan = _plan()
    return replace(
        plan,
        slots=tuple(
            replace(
                slot,
                saved_suggestion_id="recipe_import_imp-9",
                saved_recipe=recipe or _saved_recipe(),
                is_skipped=skipped,
            )
            if slot.id == "slot-2-1"
            else slot
            for slot in plan.slots
        ),
    )


@pytest.mark.asyncio
async def test_saved_recipe_lines_join_the_list_scaled_to_the_plans_people():
    categories = await WeeklyGroceryService().calculate(_Uow(), _plan_with_saved())
    items = {item.name: item for category in categories for item in category.items}

    # Catalog tomato 150 g x 2 people + saved 400 g x 2/4 people, minus 100 g.
    tomato = items["Tomato"]
    assert tomato.ingredient_id == 7
    assert tomato.total_needed == 500
    assert sorted(day["day_index"] for day in tomato.daily_amounts) == [0, 2]
    eggs = items["Eggs"]
    assert (eggs.ingredient_id, eggs.unit, eggs.total_needed) == (
        deterministic_ingredient_id("Eggs"),
        "piece",
        4,
    )
    assert eggs.quantity_confidence == "verified"
    assert items["Fish sauce"].total_needed == 10
    assert "Salt" not in items


@pytest.mark.asyncio
async def test_skipped_saved_slot_buys_nothing():
    categories = await WeeklyGroceryService().calculate(
        _Uow(), _plan_with_saved(skipped=True)
    )
    names = {item.name for category in categories for item in category.items}

    assert names == {"Tomato"}


def test_ai_meal_idea_amounts_count_as_one_estimated_serving():
    from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot

    idea = SavedRecipeSnapshot.from_saved_suggestion(
        "sugg-1",
        {
            "meal_name": "Tofu bowl",
            "macros": {"protein": 20, "carbs": 30, "fat": 10},
            "ingredients": [{"name": "Tofu", "amount": 150, "unit": "g"}],
        },
    )

    (item,) = WeeklyGroceryService().project(_plan_with_saved(recipe=idea), ())
    assert (item.name, item.total_needed, item.quantity_confidence) == (
        "Tofu",
        300,
        "estimated",
    )


def _merge_slot(recipe_id: str, lines: tuple, day_index: int = 0) -> GrocerySlot:
    return GrocerySlot(
        recipe_id=recipe_id,
        publication_status="published",
        nutrition_status="ready",
        is_active=True,
        base_servings=1,
        serving_confidence="verified",
        people=1,
        ingredients=lines,
        day_index=day_index,
    )


def _line(position: int, name: str, grams: str, food_reference_id: int | None):
    return GroceryIngredient(
        position=position,
        food_reference_id=food_reference_id,
        name=name,
        quantity=Decimal(grams),
        unit="g",
        category="protein",
    )


def test_the_same_food_under_two_names_becomes_one_line():
    items = aggregate_grocery(
        [
            _merge_slot("r1", (_line(0, "thịt heo", "200", 11),), day_index=0),
            _merge_slot("r2", (_line(0, "thịt lợn", "300", 12),), day_index=2),
        ],
        [],
    )

    assert len(items) == 1
    item = items[0]
    assert item.total_needed == 500.0
    # One wording, and the day view reads the same merged line.
    assert item.name in {"thịt heo", "thịt lợn"}
    assert [(day, needed) for day, needed, _ in item.daily_amounts] == [
        (0, 200.0),
        (2, 300.0),
    ]


def test_the_merged_line_keeps_a_real_catalog_id():
    items = aggregate_grocery(
        [
            _merge_slot("r1", (_line(0, "hành ống", "50", None),)),
            _merge_slot("r2", (_line(0, "hành lá", "50", 31),)),
        ],
        [],
    )

    assert len(items) == 1
    assert items[0].ingredient_id == 31


def test_the_most_used_wording_names_the_merged_line():
    items = aggregate_grocery(
        [
            _merge_slot("r1", (_line(0, "thịt lợn", "100", 11),)),
            _merge_slot("r2", (_line(0, "thịt lợn", "100", 11),)),
            _merge_slot("r3", (_line(0, "thịt heo", "100", 12),)),
        ],
        [],
    )

    assert [item.name for item in items] == ["thịt lợn"]


def test_english_lines_are_never_relabelled_in_vietnamese():
    items = aggregate_grocery(
        [_merge_slot("r1", (_line(0, "Tomato", "100", 21),))],
        [],
    )

    assert [item.name for item in items] == ["Tomato"]


def test_pantry_stock_and_ticks_follow_every_merged_food_id():
    items = aggregate_grocery(
        [
            _merge_slot("r1", (_line(0, "thịt heo", "200", 11),)),
            _merge_slot("r2", (_line(0, "thịt lợn", "300", 12),)),
        ],
        [
            PantryAvailability(
                food_reference_id=12,
                available_amount=Decimal("500"),
                available_unit="g",
            )
        ],
        [GroceryInteraction(food_reference_id=12, checked=True)],
    )

    assert len(items) == 1
    assert items[0].status == "owned"
    assert items[0].checked is True


def test_different_cuts_stay_separate_purchases():
    items = aggregate_grocery(
        [
            _merge_slot("r1", (_line(0, "thịt ba chỉ", "200", 11),)),
            _merge_slot("r2", (_line(0, "sườn heo", "300", 13),)),
        ],
        [],
    )

    assert len(items) == 2


def test_preparation_words_do_not_split_a_line():
    items = aggregate_grocery(
        [
            _merge_slot("r1", (_line(0, "Cà rốt (thái lát)", "100", None),)),
            _merge_slot("r2", (_line(0, "cà rốt", "150", None),)),
        ],
        [],
    )

    assert len(items) == 1
    assert items[0].total_needed == 250.0
