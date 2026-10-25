"""A typed dish name must survive every locale read."""

import json
import uuid
from datetime import datetime

from src.api.mappers.meal_mapper import MealMapper
from src.domain.model import FoodItem, Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.model.meal import MealTranslation
from src.domain.services.localized_display_name import (
    carry_dish_name_to_locales,
    display_dish_name,
    is_dish_rename,
)


def _meal(
    *,
    dish_name="Vietnamese food product",
    source="prompt",
    raw=None,
    translations=None,
):
    item = FoodItem(
        id="item-1",
        name="Ingredient",
        quantity=15,
        unit="g",
        macros=Macros(protein=0.3, carbs=1.6, fat=0.2),
    )
    return Meal(
        meal_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        status=MealStatus.READY,
        image=MealImage(
            url="https://example.com/meal.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
        ),
        source=source,
        dish_name=dish_name,
        created_at=datetime(2026, 10, 25, 8, 7),
        ready_at=datetime(2026, 10, 25, 8, 7),
        raw_gpt_json=json.dumps(raw) if raw is not None else None,
        translations=translations,
        nutrition=Nutrition(macros=item.macros, food_items=[item]),
    )


def _vi_translation(meal_id, dish_name="Sản phẩm thực phẩm Việt Nam"):
    return {
        "vi": MealTranslation(
            meal_id=meal_id,
            language="vi",
            dish_name=dish_name,
            food_items=[],
            meal_instruction=[],
            meal_ingredients=["Nguyên liệu"],
        )
    }


def _renamed(meal, name, language):
    edited = meal.mark_edited(nutrition=meal.nutrition, dish_name=name)
    assert is_dish_rename(meal, name, language)
    return carry_dish_name_to_locales(edited)


def test_translation_row_name_is_what_vietnamese_reads_show():
    meal = _meal()
    meal.translations = _vi_translation(meal.meal_id)

    assert display_dish_name(meal, "vi") == "Sản phẩm thực phẩm Việt Nam"
    assert display_dish_name(meal, "en") == "Vietnamese food product"


def test_rename_reaches_the_translation_row():
    meal = _meal()
    meal.translations = _vi_translation(meal.meal_id)

    renamed = _renamed(meal, "Bánh mì của mẹ", "vi")

    assert renamed.translations["vi"].dish_name == "Bánh mì của mẹ"
    assert display_dish_name(renamed, "vi") == "Bánh mì của mẹ"
    response = MealMapper.to_detailed_response(renamed, target_language="vi")
    assert response.dish_name == "Bánh mì của mẹ"


def test_rename_reaches_the_ai_payload_names():
    meal = _meal(
        dish_name="Bún nước",
        raw={
            "dish_name": "Rice noodle soup",
            "localized_language": "vi",
            "localized_dish_name": "Bún nước",
            "foods": [{"name": "Rice noodles", "localized_name": "Bún gạo"}],
        },
    )

    renamed = _renamed(meal, "Bún của tôi", "vi")

    raw = json.loads(renamed.raw_gpt_json)
    assert raw["dish_name"] == "Bún của tôi"
    assert raw["localized_dish_name"] == "Bún của tôi"
    assert raw["foods"] == [{"name": "Rice noodles", "localized_name": "Bún gạo"}]
    for language, food_name in (("vi", "Bún gạo"), ("en", "Rice noodles")):
        response = MealMapper.to_detailed_response(renamed, target_language=language)
        assert response.dish_name == "Bún của tôi"
        assert response.food_items[0].name == food_name


def test_english_rename_beats_the_canonical_ai_name():
    meal = _meal(dish_name="Chicken salad", raw={"dish_name": "Chicken salad"})

    renamed = _renamed(meal, "Lunch box", "en")

    response = MealMapper.to_detailed_response(renamed, target_language="en")
    assert response.dish_name == "Lunch box"


def test_echoing_the_shown_name_is_not_a_rename():
    meal = _meal()
    meal.translations = _vi_translation(meal.meal_id)

    assert not is_dish_rename(meal, "Sản phẩm thực phẩm Việt Nam", "vi")
    assert not is_dish_rename(meal, " Vietnamese food product ", "en")
    assert not is_dish_rename(meal, "", "vi")
    assert not is_dish_rename(meal, None, "vi")


def test_retyping_a_rename_the_translation_row_lost_repairs_it():
    meal = _meal(dish_name="Bánh mì của mẹ")
    meal.translations = _vi_translation(meal.meal_id)

    assert display_dish_name(meal, "vi") == "Sản phẩm thực phẩm Việt Nam"
    renamed = _renamed(meal, "Bánh mì của mẹ", "vi")

    assert display_dish_name(renamed, "vi") == "Bánh mì của mẹ"


def test_scanner_meal_without_translations_shows_its_stored_name():
    meal = _meal(
        dish_name="Phở bò",
        source="scanner",
        raw={
            "dish_name": "Beef pho",
            "localized_language": "vi",
            "localized_dish_name": "Phở bò",
            "foods": [{"name": "Beef", "localized_name": "Thịt bò"}],
        },
    )

    assert display_dish_name(meal, "en") == "Phở bò"
    assert display_dish_name(meal, "vi") == "Phở bò"
