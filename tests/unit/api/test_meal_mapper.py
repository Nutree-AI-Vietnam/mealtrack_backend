"""
Unit tests for MealMapper.
"""

import json
import uuid
from datetime import datetime

import pytest

from src.api.mappers.meal_mapper import STATUS_MAPPING, MealMapper
from src.domain.model import FoodItem, Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.model.meal import FoodItemTranslation, MealTranslation
from src.domain.model.meal.meal_translation_domain_models import (
    CURRENT_MEAL_TRANSLATION_VERSION,
)
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
)
from src.domain.services.meal_value_insight_contract import (
    IngredientValueInsight,
    MealValueInsights,
    ValueInsight,
    parse_ai_result,
)


class TestMealMapper:
    """Test suite for MealMapper."""

    def test_to_simple_response(self):
        """Test converting Meal to SimpleMealResponse."""
        meal_id = str(uuid.uuid4())

        image = MealImage(
            url="https://example.com/meal.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meal = Meal(
            meal_id=meal_id,
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=image,
            dish_name="Chicken Bowl",
            ready_at=datetime(2025, 1, 15, 12, 30),
            created_at=datetime(2025, 1, 15, 12, 0),
            nutrition=Nutrition(
                macros=Macros(protein=30, carbs=50, fat=15), food_items=[]
            ),
        )

        result = MealMapper.to_simple_response(meal)

        assert result.meal_id == meal_id
        assert result.status == "ready"
        assert result.dish_name == "Chicken Bowl"
        assert result.ready_at == datetime(2025, 1, 15, 12, 30)
        assert result.error_message is None
        assert result.created_at == datetime(2025, 1, 15, 12, 0)

    def test_to_simple_response_with_error(self):
        """Test converting failed meal to SimpleMealResponse."""
        meal_id = str(uuid.uuid4())

        image = MealImage(
            url="https://example.com/failed.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meal = Meal(
            meal_id=meal_id,
            user_id=str(uuid.uuid4()),
            status=MealStatus.FAILED,
            image=image,
            dish_name="Failed Meal",
            created_at=datetime(2025, 1, 15, 13, 0),
            error_message="Analysis failed",
        )

        result = MealMapper.to_simple_response(meal)

        assert result.status == "failed"
        assert result.error_message == "Analysis failed"

    def test_status_mapping(self):
        """Test status mapping from domain to API."""
        assert STATUS_MAPPING["PROCESSING"] == "pending"
        assert STATUS_MAPPING["ANALYZING"] == "analyzing"
        assert STATUS_MAPPING["ENRICHING"] == "analyzing"
        assert STATUS_MAPPING["READY"] == "ready"
        assert STATUS_MAPPING["FAILED"] == "failed"

    def test_to_detailed_response_with_nutrition(self):
        """Test converting Meal with nutrition to DetailedMealResponse."""
        food_items = [
            FoodItem(
                id="item-1",
                name="Chicken Breast",
                quantity=200,
                unit="g",
                macros=Macros(protein=40, carbs=0, fat=5),
                confidence=0.95,
                fdc_id=123456,
                food_reference_id=321,
                is_custom=False,
            ),
            FoodItem(
                id="item-2",
                name="Rice",
                quantity=150,
                unit="g",
                macros=Macros(protein=4, carbs=43, fat=0.4),
                confidence=0.90,
                fdc_id=789012,
                is_custom=False,
            ),
        ]

        nutrition = Nutrition(
            macros=Macros(protein=44, carbs=43, fat=5.4), food_items=food_items
        )

        image = MealImage(
            url="https://example.com/detailed.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meal_id = str(uuid.uuid4())

        meal = Meal(
            meal_id=meal_id,
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=image,
            dish_name="Chicken and Rice",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=nutrition,
        )

        result = MealMapper.to_detailed_response(
            meal, image_url="https://example.com/image.jpg"
        )

        assert result.meal_id == meal_id
        assert result.dish_name == "Chicken and Rice"
        assert result.total_calories == pytest.approx(396.6)  # derived: 44*4+43*4+5.4*9
        assert result.total_nutrition.protein == 44
        assert result.total_nutrition.carbs == 43
        assert result.total_nutrition.fat == 5.4
        assert len(result.food_items) == 2
        assert result.food_items[0].name == "Chicken Breast"
        assert result.food_items[0].display_name == "Chicken Breast"
        assert result.food_items[0].canonical_name == "Chicken Breast"
        assert result.food_items[0].fdc_id == 123456
        assert result.food_items[0].food_reference_id == 321
        assert result.food_items[1].name == "Rice"
        assert result.food_items[1].display_name == "Rice"
        assert result.food_items[1].canonical_name == "Rice"
        assert result.translation_language is None
        assert result.image_url == "https://example.com/image.jpg"
        # total_weight_grams is calculated from food items
        assert result.total_weight_grams == 350 or result.total_weight_grams is None

    def test_to_detailed_response_uses_snapshot_canonical_name(self):
        item = FoodItem(
            id="item-1",
            name="Bún gạo",
            quantity=180,
            unit="g",
            macros=Macros(protein=2.7, carbs=43.2, fat=0.4),
            food_reference_id=901,
            source_snapshot={"canonical_name": "Rice noodles", "basis": "100g"},
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/bun.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            source="prompt",
            dish_name="Bún bò",
            created_at=datetime(2025, 1, 15),
            ready_at=datetime(2025, 1, 15),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

        # Without catalog display projections loaded, the frozen snapshot
        # still supplies the canonical alias (legacy chain).
        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert result.food_items[0].name == "Bún gạo"
        assert result.food_items[0].canonical_name == "Rice noodles"

        # Once display projections are loaded for this tracked line, the
        # live catalog row outranks the frozen snapshot for name and
        # canonical_name alike.
        tracked_result = MealMapper.to_detailed_response(
            meal,
            target_language="vi",
            display_name_by_food_reference={
                901: {
                    "name": "Rice vermicelli",
                    "name_vi": "Bún gạo tươi",
                }
            },
        )

        assert tracked_result.food_items[0].name == "Bún gạo tươi"
        assert tracked_result.food_items[0].canonical_name == "Rice vermicelli"

    def test_to_detailed_response_falls_back_to_meal_image_url(self):
        """Meal detail must keep the photo when callers omit image_url."""
        image = MealImage(
            url="https://res.cloudinary.com/demo/image/upload/v1/mealtrack/abc.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=2048,
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=image,
            dish_name="Pho",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=Nutrition(
                macros=Macros(protein=20, carbs=40, fat=10),
                food_items=[],
            ),
            source="scanner",
        )

        result = MealMapper.to_detailed_response(meal, target_language="en")

        assert result.image_url == image.url

    def test_to_detailed_response_includes_canonical_source_nutrition(self):
        item = FoodItem(
            id="item-1",
            name="Pâté",
            quantity=1,
            unit="g",
            macros=Macros(protein=0.2, carbs=0.1, fat=0.3),
            food_reference_id=321,
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/pate.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            created_at=datetime(2025, 1, 15),
            ready_at=datetime(2025, 1, 15),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )
        source = FoodReferenceNutritionProjection(
            id=321,
            name="Pâté",
            source="fatsecret",
            is_verified=True,
            protein_100g=20,
            carbs_100g=10,
            fat_100g=30,
            fiber_100g=2,
            sugar_100g=1,
        )

        result = MealMapper.to_detailed_response(
            meal,
            source_nutrition_by_food_reference={321: source},
        )

        nutrition = result.food_items[0].source_nutrition
        assert nutrition is not None
        assert nutrition.protein_per_100g == 20
        assert nutrition.carbs_per_100g == 10
        assert nutrition.fat_per_100g == 30
        assert nutrition.calories_per_100g == pytest.approx(386)

    def _tracked_item_meal(self, *, food_reference_id: int, item_name: str) -> Meal:
        item = FoodItem(
            id="item-1",
            name=item_name,
            quantity=100,
            unit="g",
            macros=Macros(protein=10, carbs=5, fat=2),
            food_reference_id=food_reference_id,
        )
        return Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/tracked.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Tracked Meal",
            created_at=datetime(2026, 8, 22),
            ready_at=datetime(2026, 8, 22),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

    def test_to_detailed_response_tracked_item_uses_catalog_translation(self):
        """vi GET with name_vi shows the Vietnamese catalog name."""
        meal = self._tracked_item_meal(food_reference_id=42, item_name="Stale name")

        result = MealMapper.to_detailed_response(
            meal,
            target_language="vi",
            display_name_by_food_reference={
                42: {
                    "name": "Grilled chicken",
                    "name_vi": "Gà nướng",
                }
            },
        )

        assert result.food_items[0].name == "Gà nướng"
        assert result.food_items[0].display_name == "Gà nướng"
        assert result.food_items[0].canonical_name == "Grilled chicken"

    def test_to_detailed_response_overlays_serving_labels_for_vi(self):
        from src.app.services.serving_label import serving_phrase_key

        item = FoodItem(
            id="item-1",
            name="Rice",
            quantity=1,
            unit="cup, cooked, diced",
            macros=Macros(protein=4, carbs=45, fat=0),
            food_reference_id=42,
            allowed_units=[
                {
                    "unit": "cup, cooked, diced",
                    "gram_weight": 158.0,
                    "description": "1 cup cooked, diced",
                }
            ],
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/rice.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Rice",
            created_at=datetime(2026, 8, 22),
            ready_at=datetime(2026, 8, 22),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

        result = MealMapper.to_detailed_response(
            meal,
            target_language="vi",
            display_name_by_food_reference={
                42: {
                    "name": "Rice",
                    "name_vi": "Cơm",
                    "serving_labels": {
                        serving_phrase_key("cup, cooked, diced"): (
                            "cốc, đã nấu, thái hạt lựu"
                        )
                    },
                }
            },
        )

        labeled = result.food_items[0].allowed_units[0]
        assert labeled.unit == "cup, cooked, diced"
        assert labeled.description == "1 cup cooked, diced"
        assert labeled.display_description == "cốc, đã nấu, thái hạt lựu"

    def test_to_detailed_response_tracked_item_english_ignores_stored_locale_name(
        self,
    ):
        """en GET shows the live catalog name even if the stored item name is Vietnamese."""
        meal = self._tracked_item_meal(food_reference_id=42, item_name="Gà nướng")

        result = MealMapper.to_detailed_response(
            meal,
            target_language="en",
            display_name_by_food_reference={
                42: {
                    "name": "Grilled chicken",
                    "name_vi": "Gà nướng",
                }
            },
        )

        assert result.food_items[0].name == "Grilled chicken"
        assert result.food_items[0].canonical_name == "Grilled chicken"

    def test_to_detailed_response_tracked_item_falls_back_to_name_vi(self):
        """vi GET uses name_vi when present."""
        meal = self._tracked_item_meal(food_reference_id=42, item_name="Whatever")

        result = MealMapper.to_detailed_response(
            meal,
            target_language="vi",
            display_name_by_food_reference={
                42: {
                    "name": "Grilled chicken",
                    "name_vi": "Gà nướng",
                }
            },
        )

        assert result.food_items[0].name == "Gà nướng"

    def test_to_detailed_response_tracked_item_missing_translation_falls_back_to_english(
        self,
    ):
        """A missing ja translation shows English rather than crashing."""
        meal = self._tracked_item_meal(food_reference_id=42, item_name="Whatever")

        result = MealMapper.to_detailed_response(
            meal,
            target_language="ja",
            display_name_by_food_reference={
                42: {
                    "name": "Grilled chicken",
                    "name_vi": "Gà nướng",
                }
            },
        )

        assert result.food_items[0].name == "Grilled chicken"
        assert result.food_items[0].canonical_name == "Grilled chicken"

    def test_to_detailed_response_tracked_item_keeps_snapshot_kcal_and_macros(self):
        """Catalog display-name resolution never touches snapshot-derived macros."""
        item = FoodItem(
            id="item-1",
            name="Old label",
            quantity=100,
            unit="g",
            macros=Macros(protein=10, carbs=5, fat=2),
            food_reference_id=42,
            source_snapshot={"canonical_name": "Old label", "basis": "100g"},
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/tracked-snapshot.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Tracked Meal",
            created_at=datetime(2026, 8, 22),
            ready_at=datetime(2026, 8, 22),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

        result = MealMapper.to_detailed_response(
            meal,
            target_language="vi",
            display_name_by_food_reference={
                42: {
                    "name": "Grilled chicken",
                    "name_vi": "Gà nướng",
                }
            },
        )

        assert result.food_items[0].name == "Gà nướng"
        assert result.food_items[0].canonical_name == "Grilled chicken"
        assert result.food_items[0].nutrition.protein_g == 10
        assert result.food_items[0].nutrition.carbs_g == 5
        assert result.food_items[0].nutrition.fat_g == 2

    def test_to_detailed_response_untracked_item_ignores_unrelated_projections(self):
        """Custom items without a food_reference_id keep the stored name."""
        food_items = [
            FoodItem(
                id="item-custom",
                name="Homemade Sauce",
                quantity=50,
                unit="g",
                macros=Macros(protein=1, carbs=8, fat=2),
                is_custom=True,
            )
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/custom-untracked.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Custom Meal",
            created_at=datetime(2026, 8, 22),
            ready_at=datetime(2026, 8, 22),
            nutrition=Nutrition(
                macros=Macros(protein=1, carbs=8, fat=2), food_items=food_items
            ),
        )

        result = MealMapper.to_detailed_response(
            meal,
            display_name_by_food_reference={
                999: {"name": "Unrelated", "name_vi": None}
            },
        )

        assert result.food_items[0].name == "Homemade Sauce"

    def test_to_detailed_response_uses_same_call_locale_without_translation_row(self):
        item = FoodItem(
            id="item-1",
            name="Bún gạo đã nấu",
            quantity=180,
            unit="g",
            macros=Macros(protein=4, carbs=50, fat=1),
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/noodles.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            source="scanner",
            dish_name="Bún nước",
            created_at=datetime(2025, 1, 15),
            ready_at=datetime(2025, 1, 15),
            raw_gpt_json=json.dumps(
                {
                    "dish_name": "Vietnamese rice noodle soup",
                    "localized_language": "vi",
                    "localized_dish_name": "Bún nước",
                    "foods": [
                        {
                            "name": "Cooked rice noodles",
                            "localized_name": "Bún gạo đã nấu",
                        }
                    ],
                }
            ),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert result.dish_name == "Bún nước"
        assert result.food_items[0].name == "Bún gạo đã nấu"
        assert result.food_items[0].display_name == "Bún gạo đã nấu"
        assert result.food_items[0].canonical_name == "Cooked rice noodles"
        assert result.translation_language == "vi"
        assert result.translations is None

        english_result = MealMapper.to_detailed_response(meal, target_language="en")

        assert english_result.dish_name == "Bún nước"
        assert english_result.food_items[0].name == "Bún gạo đã nấu"
        assert english_result.food_items[0].canonical_name == "Cooked rice noodles"

    @pytest.mark.parametrize(
        ("language", "localized_dish", "localized_food"),
        [
            ("de", "Reisnudelsuppe", "Reisnudeln"),
            ("es", "Sopa de fideos de arroz", "Fideos de arroz"),
            ("fr", "Soupe de nouilles de riz", "Nouilles de riz"),
            ("ja", "米麺スープ", "米麺"),
            ("vi", "Bún nước", "Bún gạo"),
            ("zh", "米粉汤", "米粉"),
        ],
    )
    def test_to_detailed_response_round_trips_every_supported_locale(
        self, language, localized_dish, localized_food
    ):
        item = FoodItem(
            id="item-1",
            name=localized_food,
            quantity=180,
            unit="g",
            macros=Macros(protein=4, carbs=50, fat=1),
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/noodles.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            source="scanner",
            dish_name=localized_dish,
            created_at=datetime(2025, 1, 15),
            ready_at=datetime(2025, 1, 15),
            raw_gpt_json=json.dumps(
                {
                    "dish_name": "Rice noodle soup",
                    "localized_language": language,
                    "localized_dish_name": localized_dish,
                    "foods": [
                        {
                            "name": "Rice noodles",
                            "localized_name": localized_food,
                        }
                    ],
                }
            ),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

        localized = MealMapper.to_detailed_response(
            meal,
            target_language=language,
        )
        english = MealMapper.to_detailed_response(meal, target_language="en")

        assert localized.dish_name == localized_dish
        assert localized.food_items[0].name == localized_food
        assert localized.food_items[0].canonical_name == "Rice noodles"
        assert english.dish_name == localized_dish
        assert english.food_items[0].name == localized_food
        assert localized.total_calories == english.total_calories
        assert (
            localized.total_nutrition.model_dump()
            == english.total_nutrition.model_dump()
        )
        assert localized.translations is None

    def test_to_detailed_response_keeps_english_image_names_for_other_locale(self):
        item = FoodItem(
            id="item-1",
            name="Rice noodles",
            quantity=180,
            unit="g",
            macros=Macros(protein=4, carbs=50, fat=1),
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/noodles.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            source="scanner",
            dish_name="Rice noodle soup",
            created_at=datetime(2025, 1, 15),
            ready_at=datetime(2025, 1, 15),
            raw_gpt_json=json.dumps(
                {
                    "dish_name": "Rice noodle soup",
                    "foods": [{"name": "Rice noodles"}],
                }
            ),
            nutrition=Nutrition(macros=item.macros, food_items=[item]),
        )

        result = MealMapper.to_detailed_response(meal, target_language="de")

        assert result.dish_name == "Rice noodle soup"
        assert result.food_items[0].name == "Rice noodles"
        assert result.translation_language is None

    def test_to_detailed_response_uses_item_id_translations_when_list_is_stale(self):
        """Translated food item IDs preserve locale after ingredient add/remove."""
        food_items = [
            FoodItem(
                id="item-1",
                name="Chicken Breast",
                quantity=200,
                unit="g",
                macros=Macros(protein=40, carbs=0, fat=5),
            ),
            FoodItem(
                id="item-2",
                name="Rice",
                quantity=150,
                unit="g",
                macros=Macros(protein=4, carbs=43, fat=0.4),
            ),
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/detailed.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Chicken and Rice",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=Nutrition(
                macros=Macros(protein=44, carbs=43, fat=5.4),
                food_items=food_items,
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm gà",
                    meal_ingredients=["Ức gà", "Cơm", "Bông cải"],
                    food_items=[
                        FoodItemTranslation(
                            food_item_id="item-1",
                            name="Ức gà",
                        ),
                        FoodItemTranslation(
                            food_item_id="item-2",
                            name="Cơm",
                        ),
                    ],
                )
            },
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert result.dish_name == "Cơm gà"
        assert [item.name for item in result.food_items] == ["Ức gà", "Cơm"]
        assert [item.display_name for item in result.food_items] == ["Ức gà", "Cơm"]
        assert [item.canonical_name for item in result.food_items] == [
            "Chicken Breast",
            "Rice",
        ]
        assert result.translation_language == "vi"

    def test_to_detailed_response_does_not_apply_leftover_english_vi_translation(
        self,
    ):
        """Parse-text names are already Vietnamese; inverted vi rows must not paint."""
        food_items = [
            FoodItem(
                id="item-1",
                name="Cơm tấm",
                quantity=1,
                unit="dĩa",
                macros=Macros(protein=12, carbs=70, fat=4),
            ),
            FoodItem(
                id="item-2",
                name="Bì heo",
                quantity=1,
                unit="phần",
                macros=Macros(protein=6, carbs=2, fat=8),
            ),
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/com-tam.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            source="prompt",
            dish_name="Cơm tấm với sườn, bì, chả",
            ready_at=datetime(2026, 8, 22, 13, 50),
            created_at=datetime(2026, 8, 22, 13, 50),
            nutrition=Nutrition(
                macros=Macros(protein=18, carbs=72, fat=12),
                food_items=food_items,
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm tấm với sườn, bì, chả",
                    meal_ingredients=["Broken rice", "Shredded pork skin"],
                    food_items=[
                        FoodItemTranslation(
                            food_item_id="item-1",
                            name="Broken rice",
                        ),
                        FoodItemTranslation(
                            food_item_id="item-2",
                            name="Shredded pork skin",
                        ),
                    ],
                )
            },
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert result.dish_name == "Cơm tấm với sườn, bì, chả"
        assert [item.name for item in result.food_items] == ["Cơm tấm", "Bì heo"]
        assert [item.canonical_name for item in result.food_items] == [
            "Cơm tấm",
            "Bì heo",
        ]

    def test_to_detailed_response_falls_back_per_item_to_safe_legacy_translation(
        self,
    ):
        """Missing ID translations can use legacy names when order is still aligned."""
        food_items = [
            FoodItem(
                id="item-1",
                name="Chicken Breast",
                quantity=200,
                unit="g",
                macros=Macros(protein=40, carbs=0, fat=5),
            ),
            FoodItem(
                id="item-2",
                name="Rice",
                quantity=150,
                unit="g",
                macros=Macros(protein=4, carbs=43, fat=0.4),
            ),
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/detailed.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Chicken and Rice",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=Nutrition(
                macros=Macros(protein=44, carbs=43, fat=5.4),
                food_items=food_items,
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm gà",
                    meal_ingredients=["Ức gà", "Cơm"],
                    food_items=[
                        FoodItemTranslation(
                            food_item_id="item-1",
                            name="Gà",
                        )
                    ],
                )
            },
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert [item.name for item in result.food_items] == ["Gà", "Cơm"]
        assert [item.display_name for item in result.food_items] == ["Gà", "Cơm"]
        assert [item.canonical_name for item in result.food_items] == [
            "Chicken Breast",
            "Rice",
        ]
        assert result.translation_language == "vi"

    def test_to_detailed_response_keeps_canonical_name_with_legacy_translation(self):
        """Legacy ordered fallback localizes display fields only."""
        food_items = [
            FoodItem(
                id="item-1",
                name="Chicken Breast",
                quantity=200,
                unit="g",
                macros=Macros(protein=40, carbs=0, fat=5),
            ),
            FoodItem(
                id="item-2",
                name="Rice",
                quantity=150,
                unit="g",
                macros=Macros(protein=4, carbs=43, fat=0.4),
            ),
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/detailed.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Chicken and Rice",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=Nutrition(
                macros=Macros(protein=44, carbs=43, fat=5.4),
                food_items=food_items,
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm gà",
                    meal_ingredients=["Ức gà", "Cơm"],
                    food_items=[],
                )
            },
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert [item.name for item in result.food_items] == ["Ức gà", "Cơm"]
        assert [item.display_name for item in result.food_items] == ["Ức gà", "Cơm"]
        assert [item.canonical_name for item in result.food_items] == [
            "Chicken Breast",
            "Rice",
        ]
        assert result.translation_language == "vi"

    def test_to_detailed_response_ignores_stale_legacy_translation_count(self):
        """Stale ordered fallback must not translate by the wrong index."""
        food_items = [
            FoodItem(
                id="item-2",
                name="Rice",
                quantity=150,
                unit="g",
                macros=Macros(protein=4, carbs=43, fat=0.4),
            )
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/detailed.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Rice",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=Nutrition(
                macros=Macros(protein=4, carbs=43, fat=0.4),
                food_items=food_items,
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm",
                    meal_ingredients=["Ức gà", "Cơm"],
                    food_items=[],
                )
            },
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert result.food_items[0].name == "Rice"
        assert result.food_items[0].display_name == "Rice"
        assert result.food_items[0].canonical_name == "Rice"
        assert result.translation_language == "vi"

    def test_to_detailed_response_does_not_apply_pre_cutover_translation(self):
        food_item = FoodItem(
            id="item-2",
            name="Rice",
            quantity=150,
            unit="g",
            macros=Macros(protein=4, carbs=43, fat=0.4),
        )
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/detailed.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Rice",
            ready_at=datetime(2025, 1, 15, 14, 0),
            created_at=datetime(2025, 1, 15, 13, 30),
            nutrition=Nutrition(
                macros=Macros(protein=4, carbs=43, fat=0.4),
                food_items=[food_item],
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm",
                    meal_ingredients=["Cơm"],
                    food_items=[],
                    translation_version=CURRENT_MEAL_TRANSLATION_VERSION - 1,
                )
            },
        )

        result = MealMapper.to_detailed_response(meal, target_language="vi")

        assert result.dish_name == "Rice"
        assert result.food_items[0].name == "Rice"
        assert result.translation_language is None

    def test_to_detailed_response_with_custom_food_item(self):
        """Test detailed response with custom food item."""
        food_items = [
            FoodItem(
                id="item-custom",
                name="Homemade Sauce",
                quantity=50,
                unit="g",
                macros=Macros(protein=1, carbs=8, fat=2, fiber=1, sugar=3),
                confidence=1.0,
                fdc_id=None,
                is_custom=True,
            )
        ]

        nutrition = Nutrition(
            macros=Macros(protein=1, carbs=8, fat=2, fiber=1, sugar=3),
            food_items=food_items,
        )

        image = MealImage(
            url="https://example.com/custom.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=image,
            dish_name="Custom Meal",
            ready_at=datetime(2025, 1, 15, 15, 0),
            created_at=datetime(2025, 1, 15, 14, 30),
            nutrition=nutrition,
        )

        result = MealMapper.to_detailed_response(meal)

        assert len(result.food_items) == 1
        assert result.food_items[0].is_custom is True
        assert result.food_items[0].fdc_id is None
        assert result.food_items[0].custom_nutrition is not None
        assert result.food_items[0].custom_nutrition.calories_per_100g == pytest.approx(
            104.0
        )  # derived fiber-aware then scaled to 100g
        assert result.food_items[0].custom_nutrition.fiber_per_100g == pytest.approx(
            2.0
        )
        assert result.food_items[0].custom_nutrition.sugar_per_100g == pytest.approx(
            6.0
        )

    def test_to_detailed_response_includes_food_label_metadata(self):
        """Food-label meal details expose serving and package metadata."""
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/label.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Protein Bar",
            ready_at=datetime(2025, 1, 15, 15, 0),
            created_at=datetime(2025, 1, 15, 14, 30),
            source="food_label",
            food_label_metadata={
                "product_name": "Protein Bar",
                "brand": "Example",
                "serving_size": {
                    "display_text": "1 bar (55g)",
                    "grams": 55,
                },
                "servings_per_package": 8,
                "label_calories_per_serving": 230,
                "confidence": 0.92,
                "label_notes": ["Calories differ from derived macros."],
            },
            raw_gpt_json=json.dumps(
                {
                    "product_name": "Protein Bar",
                    "brand": "Example",
                    "serving_size": {
                        "display_text": "1 bar (55g)",
                        "grams": 55,
                    },
                    "servings_per_package": 8,
                    "label_calories_per_serving": 230,
                    "confidence": 0.92,
                    "label_notes": ["Calories differ from derived macros."],
                }
            ),
            nutrition=Nutrition(
                macros=Macros(protein=10, carbs=20, fat=5, fiber=4, sugar=8),
                food_items=[
                    FoodItem(
                        id="label-item",
                        name="Protein Bar",
                        quantity=55,
                        unit="g",
                        macros=Macros(
                            protein=10,
                            carbs=20,
                            fat=5,
                            fiber=4,
                            sugar=8,
                        ),
                        is_custom=True,
                    )
                ],
            ),
        )

        result = MealMapper.to_detailed_response(meal)

        assert result.source == "food_label"
        assert result.total_calories == pytest.approx(230)
        assert result.food_items[0].nutrition.calories == pytest.approx(230)
        assert result.food_items[0].custom_nutrition.calories_per_100g == (
            pytest.approx(418.1818)
        )
        assert result.food_label_metadata is not None
        assert result.food_label_metadata.product_name == "Protein Bar"
        assert result.food_label_metadata.brand == "Example"
        assert result.food_label_metadata.serving_size.display_text == "1 bar (55g)"
        assert result.food_label_metadata.serving_size.grams == 55
        assert result.food_label_metadata.servings_per_package == 8
        assert result.food_label_metadata.label_calories_per_serving == 230
        assert result.food_label_metadata.confidence == 0.92
        assert result.food_label_metadata.label_notes == [
            "Calories differ from derived macros."
        ]

    def test_to_detailed_response_prefers_stored_food_label_metadata(self):
        """Food-label metadata does not depend on raw AI JSON once persisted."""
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/label.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
            ),
            dish_name="Protein Bar",
            ready_at=datetime(2025, 1, 15, 15, 0),
            created_at=datetime(2025, 1, 15, 14, 30),
            source="food_label",
            raw_gpt_json=None,
            food_label_metadata={
                "product_name": "Protein Bar",
                "brand": "Example",
                "serving_size": {
                    "display_text": "1 bar (55g)",
                    "grams": 55,
                },
                "servings_per_package": 8,
                "label_calories_per_serving": 230,
                "confidence": 0.92,
                "label_notes": ["Stored metadata"],
            },
            nutrition=Nutrition(
                macros=Macros(protein=10, carbs=20, fat=5, fiber=4, sugar=8),
                food_items=[
                    FoodItem(
                        id="label-item",
                        name="Protein Bar",
                        quantity=55,
                        unit="g",
                        macros=Macros(
                            protein=10,
                            carbs=20,
                            fat=5,
                            fiber=4,
                            sugar=8,
                        ),
                        is_custom=True,
                    )
                ],
            ),
        )

        result = MealMapper.to_detailed_response(meal)

        assert result.food_label_metadata is not None
        assert result.food_label_metadata.serving_size.grams == 55
        assert result.food_label_metadata.servings_per_package == 8
        assert result.food_label_metadata.label_notes == ["Stored metadata"]

    def test_to_detailed_response_custom_nutrition_uses_grams_for_large_units(self):
        """Custom nutrition is projected per 100g, not per serving count."""
        food_items = [
            FoodItem(
                id="item-eggs",
                name="Eggs",
                quantity=20,
                unit="large",
                macros=Macros(protein=126, carbs=7, fat=96),
                confidence=0.8,
                fdc_id=None,
                is_custom=True,
            )
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/eggs.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Omelette",
            ready_at=datetime(2025, 1, 15, 15, 0),
            created_at=datetime(2025, 1, 15, 14, 30),
            nutrition=Nutrition(
                macros=Macros(protein=126, carbs=7, fat=96),
                food_items=food_items,
            ),
        )

        result = MealMapper.to_detailed_response(meal)

        custom = result.food_items[0].custom_nutrition
        assert custom is not None
        assert custom.protein_per_100g == pytest.approx(12.6)
        assert custom.carbs_per_100g == pytest.approx(0.7)
        assert custom.fat_per_100g == pytest.approx(9.6)

    def test_to_detailed_response_custom_nutrition_does_not_treat_nhanh_as_one_gram(
        self,
    ):
        """Unknown culinary units must not explode per-100g calories 100x."""
        food_items = [
            FoodItem(
                id="item-cilantro",
                name="Cilantro",
                quantity=1,
                unit="nhánh",
                macros=Macros(protein=5, carbs=5, fat=0),
                confidence=0.8,
                is_custom=True,
                allowed_units=[
                    {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
                    {"unit": "nhánh", "gram_weight": 100.0, "description": "1 nhánh"},
                ],
            )
        ]
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/cilantro.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Herbs",
            ready_at=datetime(2025, 1, 15, 15, 0),
            created_at=datetime(2025, 1, 15, 14, 30),
            nutrition=Nutrition(
                macros=Macros(protein=5, carbs=5, fat=0),
                food_items=food_items,
            ),
        )

        result = MealMapper.to_detailed_response(meal)

        custom = result.food_items[0].custom_nutrition
        assert custom is not None
        assert custom.protein_per_100g == pytest.approx(5.0)
        assert custom.calories_per_100g == pytest.approx(40.0)
        assert custom.calories_per_100g < 100

    def test_to_detailed_response_without_nutrition(self):
        """Test detailed response when nutrition is None."""
        image = MealImage(
            url="https://example.com/processing.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.PROCESSING,
            image=image,
            dish_name="Processing Meal",
            created_at=datetime(2025, 1, 15, 16, 0),
        )

        result = MealMapper.to_detailed_response(meal)

        assert result.total_calories == 0
        assert result.food_items == []
        assert result.total_nutrition is None

    def test_to_meal_list_response(self):
        """Test converting list of meals to MealListResponse."""
        image1 = MealImage(
            url="https://example.com/meal1.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        image2 = MealImage(
            url="https://example.com/meal2.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meals = [
            Meal(
                meal_id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                status=MealStatus.READY,
                image=image1,
                dish_name="Meal 1",
                ready_at=datetime(2025, 1, 15, 12, 0),
                created_at=datetime(2025, 1, 15, 11, 30),
                nutrition=Nutrition(
                    macros=Macros(protein=30, carbs=40, fat=10),
                    food_items=[
                        FoodItem(
                            id="item-1",
                            name="Item 1",
                            quantity=100,
                            unit="g",
                            macros=Macros(protein=30, carbs=40, fat=10),
                            confidence=0.9,
                        )
                    ],
                ),
            ),
            Meal(
                meal_id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                status=MealStatus.PROCESSING,
                image=image2,
                dish_name="Meal 2",
                created_at=datetime(2025, 1, 15, 13, 0),
            ),
        ]

        result = MealMapper.to_meal_list_response(
            meals=meals,
            total=10,
            page=1,
            page_size=2,
            image_urls={"meal-1": "https://example.com/meal1.jpg"},
        )

        assert result.total == 10
        assert result.page == 1
        assert result.page_size == 2
        assert result.total_pages == 5
        assert len(result.meals) == 2

    def test_map_nutrition_from_dict(self):
        """Test creating Nutrition from dictionary."""
        nutrition_dict = {
            "calories": 500,
            "protein_g": 35,
            "carbs_g": 55,
            "fat_g": 15,
            "sugar_g": 10,
            "sodium_mg": 400,
        }

        result = MealMapper.map_nutrition_from_dict(nutrition_dict)

        # calories is derived: 35*4 + 55*4 + 15*9 = 495.0
        assert result.calories == pytest.approx(495.0)
        assert result.macros.protein == 35
        assert result.macros.carbs == 55
        assert result.macros.fat == 15
        assert result.micros is not None
        assert result.micros.sodium == 400

    def test_map_food_item_from_dict(self):
        """Test creating FoodItem from dictionary."""
        item_dict = {
            "id": "item-456",
            "name": "Salmon",
            "category": "protein",
            "quantity": 180,
            "unit": "g",
            "description": "Fresh salmon fillet",
            "nutrition": {
                "nutrition_id": "nutr-456",
                "calories": 350,
                "protein_g": 40,
                "carbs_g": 0,
                "fat_g": 20,
                "sugar_g": 0,
                "sodium_mg": 80,
            },
        }

        result = MealMapper.map_food_item_from_dict(item_dict)

        assert result.id == "item-456"
        assert result.name == "Salmon"
        assert result.quantity == 180
        assert result.unit == "g"
        # calories is derived: 40*4 + 0*4 + 20*9 = 340.0
        assert result.calories == pytest.approx(340.0)
        assert result.macros.protein == 40
        assert result.macros.carbs == 0
        assert result.macros.fat == 20

    def test_to_daily_nutrition_response(self):
        """Test converting daily macros data to DailyNutritionResponse."""
        daily_macros_data = {
            "date": "2025-01-15",
            "user_id": "user-123",
            "target_calories": 2000.0,
            "target_macros": {"protein": 150.0, "carbs": 250.0, "fat": 67.0},
            "total_calories": 1500.0,
            "total_protein": 100.0,
            "total_carbs": 180.0,
            "total_fat": 50.0,
        }

        result = MealMapper.to_daily_nutrition_response(daily_macros_data)

        assert result.date == "2025-01-15"
        assert result.target_calories == 2000.0
        assert result.target_macros.protein == 150.0
        assert result.consumed_calories == 1500.0
        assert result.consumed_macros.protein == 100.0
        assert result.remaining_calories == 500.0
        assert result.remaining_macros.protein == 50.0
        assert result.completion_percentage["calories"] == 75.0
        assert result.completion_percentage["protein"] == pytest.approx(66.67, rel=0.01)
        # Gross/burn fields default safely when the payload omits them.
        assert result.food_calories is None
        assert result.movement_kcal_burned == 0.0

    def test_to_daily_nutrition_response_surfaces_gross_food_and_burn(self):
        """consumed_calories stays NET; gross food_calories + burn are surfaced.

        Pins the contract that lets burn-owning clients avoid double-subtraction:
        consumed_calories is net (food - burn), food_calories is the gross intake.
        """
        daily_macros_data = {
            "date": "2025-01-17",
            "user_id": "user-789",
            "target_calories": 2000.0,
            "target_macros": {"protein": 150.0, "carbs": 250.0, "fat": 67.0},
            "total_calories": 1500.0,  # NET (gross 1800 - 300 burn)
            "food_calories": 1800.0,
            "movement_kcal_burned": 300.0,
            "total_protein": 100.0,
            "total_carbs": 180.0,
            "total_fat": 50.0,
        }

        result = MealMapper.to_daily_nutrition_response(daily_macros_data)

        assert result.consumed_calories == 1500.0  # NET, unchanged
        assert result.food_calories == 1800.0  # GROSS surfaced separately
        assert result.movement_kcal_burned == 300.0

    def test_to_daily_nutrition_response_missing_target_calories(self):
        """Test error when target_calories is missing."""
        daily_macros_data = {"target_macros": {"protein": 150, "carbs": 250, "fat": 67}}

        with pytest.raises(Exception, match="User profile not found"):
            MealMapper.to_daily_nutrition_response(daily_macros_data)

    def test_to_daily_nutrition_response_over_target(self):
        """Test when consumed calories exceed target."""
        daily_macros_data = {
            "date": "2025-01-16",
            "user_id": "user-456",
            "target_calories": 2000.0,
            "target_macros": {"protein": 150.0, "carbs": 250.0, "fat": 67.0},
            "total_calories": 2500.0,
            "total_protein": 180.0,
            "total_carbs": 300.0,
            "total_fat": 80.0,
        }

        result = MealMapper.to_daily_nutrition_response(daily_macros_data)

        assert result.remaining_calories == 0  # Should not be negative
        assert result.remaining_macros.protein == 0
        assert result.completion_percentage["calories"] == 125.0

    def test_to_detailed_response_with_legacy_nutrition_structure(self):
        """Test detailed response with legacy nutrition structure (direct properties)."""
        # Create nutrition with direct protein/carbs/fat properties
        nutrition = Nutrition(
            macros=Macros(protein=30, carbs=45, fat=12), food_items=[]
        )

        image = MealImage(
            url="https://example.com/legacy.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
            width=800,
            height=600,
        )

        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=image,
            dish_name="Legacy Meal",
            ready_at=datetime(2025, 1, 15, 17, 0),
            created_at=datetime(2025, 1, 15, 16, 30),
            nutrition=nutrition,
        )

        result = MealMapper.to_detailed_response(meal)

        assert result.total_nutrition is not None
        assert result.total_nutrition.protein == 30
        assert result.total_nutrition.carbs == 45
        assert result.total_nutrition.fat == 12

    def test_to_detailed_response_serializes_value_insights(self):
        """Detailed responses serialize provided value insights."""
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/insights.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Egg Rice Bowl",
            ready_at=datetime(2025, 1, 15, 17, 0),
            created_at=datetime(2025, 1, 15, 16, 30),
            nutrition=Nutrition(
                macros=Macros(protein=31, carbs=54, fat=14, fiber=7),
                confidence_score=0.92,
                food_items=[
                    FoodItem(
                        id="egg-1",
                        name="Egg",
                        quantity=2,
                        unit="piece",
                        macros=Macros(protein=16, carbs=1, fat=10),
                        confidence=0.9,
                    ),
                    FoodItem(
                        id="rice-1",
                        name="Rice",
                        quantity=180,
                        unit="g",
                        macros=Macros(protein=4, carbs=52, fat=1),
                        confidence=0.9,
                    ),
                    FoodItem(
                        id="sauce-1",
                        name="Sauce",
                        quantity=20,
                        unit="g",
                        macros=Macros(protein=0, carbs=8, fat=0),
                        confidence=0.9,
                    ),
                ],
            ),
        )

        result = MealMapper.to_detailed_response(
            meal,
            value_insights=MealValueInsights(
                meal_bullets=[
                    ValueInsight(
                        text="Strong protein helps fullness; add plants for more volume.",
                        category="benefit",
                        highlights=["fullness"],
                    )
                ],
                ingredient_insights=[
                    IngredientValueInsight(
                        ingredient_name="Rice",
                        text="Rice gives carbs for steady energy; portion size shapes fullness.",
                        category="balance",
                        highlights=["steady energy"],
                    )
                ],
            ),
        )

        assert result.value_insights is not None
        assert len(result.value_insights.meal_bullets) <= 2
        assert len(result.value_insights.ingredient_insights) <= 2
        assert all(len(item.text) <= 120 for item in result.value_insights.meal_bullets)
        assert all(
            len(item.text) <= 120 for item in result.value_insights.ingredient_insights
        )
        assert {item.category.value for item in result.value_insights.meal_bullets} <= {
            "benefit",
            "caution",
            "balance",
        }
        assert result.value_insights.ingredient_insights[0].ingredient_name == "Rice"

    def test_to_detailed_response_serializes_translated_insight_names(self):
        """Ingredient insight labels can follow translated response names."""
        meal = Meal(
            meal_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status=MealStatus.READY,
            image=MealImage(
                url="https://example.com/translated.jpg",
                image_id=str(uuid.uuid4()),
                format="jpeg",
                size_bytes=1024,
                width=800,
                height=600,
            ),
            dish_name="Chicken and Rice",
            ready_at=datetime(2025, 1, 15, 17, 0),
            created_at=datetime(2025, 1, 15, 16, 30),
            nutrition=Nutrition(
                macros=Macros(protein=44, carbs=43, fat=5.4),
                food_items=[
                    FoodItem(
                        id="item-1",
                        name="Chicken Breast",
                        quantity=200,
                        unit="g",
                        macros=Macros(protein=40, carbs=0, fat=5),
                    ),
                    FoodItem(
                        id="item-2",
                        name="Rice",
                        quantity=150,
                        unit="g",
                        macros=Macros(protein=4, carbs=43, fat=0.4),
                    ),
                ],
            ),
            translations={
                "vi": MealTranslation(
                    meal_id="meal-1",
                    language="vi",
                    dish_name="Cơm gà",
                    food_items=[
                        FoodItemTranslation(food_item_id="item-1", name="Ức gà"),
                        FoodItemTranslation(food_item_id="item-2", name="Cơm"),
                    ],
                )
            },
        )

        result = MealMapper.to_detailed_response(
            meal,
            target_language="vi",
            value_insights=MealValueInsights(
                meal_bullets=[
                    ValueInsight(
                        text="Giàu protein giúp no lâu và ít béo giúp nhẹ bụng.",
                        category="benefit",
                        highlights=["no lâu"],
                    )
                ],
                ingredient_insights=[
                    IngredientValueInsight(
                        ingredient_name="Ức gà",
                        text="Ức gà bổ sung protein nạc giúp phục hồi và ít béo giúp nhẹ bụng.",
                        category="benefit",
                        highlights=["phục hồi"],
                    )
                ],
            ),
        )

        assert result.value_insights is not None
        assert result.value_insights.ingredient_insights[0].ingredient_name == "Ức gà"

    def test_meal_value_insight_service_parses_bounded_ai_output(self):
        """AI insight output is capped to the v1 response bounds."""
        insights = parse_ai_result(
            {
                "meal_bullets": [
                    {
                        "text": "Egg gives protein for fullness and fat for satiety.",
                        "category": "benefit",
                        "highlights": ["fullness"],
                    },
                    {
                        "text": "Add vegetables for fiber-supported digestion and volume for fullness.",
                        "category": "balance",
                        "highlights": ["digestion"],
                    },
                    {
                        "text": "This third item should be ignored.",
                        "category": "caution",
                        "highlights": ["third item"],
                    },
                ],
                "ingredient_insights": [
                    {
                        "ingredient_name": "Egg",
                        "text": "Provides protein for fullness and richness for satisfaction.",
                        "category": "benefit",
                        "highlights": ["fullness"],
                    },
                    {
                        "ingredient_name": "Rice",
                        "text": "Rice offers carbs for energy and starch for quick fuel.",
                        "category": "balance",
                        "highlights": ["energy"],
                    },
                    {
                        "ingredient_name": "Sauce",
                        "text": "This third ingredient should be ignored.",
                        "category": "caution",
                        "highlights": ["third ingredient"],
                    },
                ],
            }
        )

        assert insights is not None
        assert len(insights.meal_bullets) <= 2
        assert len(insights.ingredient_insights) <= 2
        assert all(len(item.text) <= 120 for item in insights.meal_bullets)
        assert all(len(item.text) <= 120 for item in insights.ingredient_insights)

    def test_meal_value_insight_service_keeps_one_body_effect_highlight(self):
        """Highlights spotlight body effects, not macro keywords."""
        insights = parse_ai_result(
            {
                "meal_bullets": [
                    {
                        "text": "Egg adds protein that supports fullness after this meal.",
                        "category": "benefit",
                        "highlights": ["fullness"],
                    }
                ],
                "ingredient_insights": [
                    {
                        "ingredient_name": "Rice",
                        "text": "Rice is high in carbs and low in protein, so fullness may fade sooner.",
                        "category": "caution",
                        "highlights": ["fullness may fade sooner"],
                    }
                ],
            }
        )

        assert insights is not None
        assert insights.meal_bullets[0].highlights == ["fullness"]
        assert insights.ingredient_insights[0].category == "caution"
        assert insights.ingredient_insights[0].highlights == [
            "fullness may fade sooner"
        ]

    def test_meal_value_insight_service_strips_category_labels_from_text(self):
        """AI category labels are metadata and should not appear in copy."""
        insights = parse_ai_result(
            {
                "meal_bullets": [
                    {
                        "text": "Benefit: High protein supports fullness.",
                        "category": "benefit",
                        "highlights": ["fullness"],
                    }
                ],
                "ingredient_insights": [
                    {
                        "ingredient_name": "Rice",
                        "text": "Balance: High carbs provide quick fuel.",
                        "category": "balance",
                        "highlights": ["quick fuel"],
                    }
                ],
            }
        )

        assert insights is not None
        assert insights.meal_bullets[0].text == "High protein supports fullness."
        assert insights.ingredient_insights[0].text == (
            "High carbs provide quick fuel."
        )

    def test_meal_value_insight_service_omits_invalid_ai_output(self):
        """Invalid AI output does not produce deterministic copy."""
        insights = parse_ai_result(
            {"meal_bullets": [{"text": "", "category": "benefit"}]}
        )

        assert insights is None
