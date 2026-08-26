"""
Mapper for meal-related DTOs and domain models.
"""

import json
from typing import Any

from src.api.mappers.food_reference_display_name import (
    resolve_food_reference_display_name,
)
from src.api.schemas.response import (
    DetailedMealResponse,
    FoodItemResponse,
    FoodLabelMetadataResponse,
    FoodLabelServingSizeResponse,
    MealListResponse,
    NutritionResponse,
    SimpleMealResponse,
)
from src.api.schemas.response.daily_nutrition_response import DailyNutritionResponse
from src.app.services.serving_label import overlay_serving_labels
from src.domain.constants.languages import normalize_language
from src.domain.model.meal import Meal
from src.domain.model.meal.meal_response_localization import (
    MealResponseLocalization,
    parse_meal_response_localization,
)
from src.domain.model.meal.meal_translation_domain_models import (
    CURRENT_MEAL_TRANSLATION_VERSION,
)
from src.domain.model.nutrition import FoodItem, Macros, Micros, Nutrition
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
)
from src.domain.services.localized_display_name import keep_stored_display_name
from src.domain.services.meal_calorie_service import (
    effective_food_item_calories,
    effective_meal_calories,
)
from src.domain.services.meal_value_insight_contract import MealValueInsights
from src.domain.services.nutrition_calculation_service import (
    quantity_to_grams,
    reconcile_calories_per_100g,
)

# Status mapping from domain to API
STATUS_MAPPING = {
    "PROCESSING": "pending",
    "ANALYZING": "analyzing",
    "ENRICHING": "analyzing",
    "READY": "ready",
    "FAILED": "failed",
}


def _snapshot_canonical_name(item) -> str | None:
    snapshot = getattr(item, "source_snapshot", None) or {}
    if not isinstance(snapshot, dict):
        return None
    name = snapshot.get("canonical_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _apply_translated_food_name(
    food_item, translated_name, language: str | None
) -> None:
    if not translated_name:
        return
    if keep_stored_display_name(
        stored=food_item.name,
        translated=translated_name,
        language=language,
    ):
        return
    food_item.name = translated_name
    food_item.display_name = translated_name


class MealMapper:
    """Mapper for meal data transformation."""

    @staticmethod
    def to_simple_response(meal: Meal) -> SimpleMealResponse:
        """
        Convert Meal domain model to SimpleMealResponse DTO.

        Args:
            meal: Meal domain model

        Returns:
            SimpleMealResponse DTO
        """
        return SimpleMealResponse(
            meal_id=meal.meal_id,
            status=STATUS_MAPPING.get(meal.status.value, meal.status.value.lower()),
            dish_name=meal.dish_name,
            emoji=meal.emoji,
            meal_type=meal.meal_type,
            source=meal.source,
            ready_at=meal.ready_at,
            error_message=meal.error_message,
            created_at=meal.created_at,
        )

    @staticmethod
    def to_detailed_response(
        meal: Meal,
        image_url: str | None = None,
        target_language: str | None = None,
        value_insights: MealValueInsights | None = None,
        source_nutrition_by_food_reference: dict[int, FoodReferenceNutritionProjection]
        | None = None,
        display_name_by_food_reference: dict[int, Any] | None = None,
    ) -> DetailedMealResponse:
        """
        Convert Meal domain model to DetailedMealResponse DTO.

        Args:
            meal: Meal domain model
            image_url: Optional image URL. When omitted, falls back to the
                persisted ``meal.image.url`` so callers cannot accidentally drop
                the photo on meal-detail responses.
            target_language: ISO 639-1 code; if provided and a cached
                translation exists, translated fields are applied to the response.
            display_name_by_food_reference: Id-keyed catalog display
                projections (``get_display_projections``). Items whose
                ``food_reference_id`` is present here get their name/
                display_name/canonical_name from the live catalog row instead
                of the snapshot/raw-payload/meal-translation chain.

        Returns:
            DetailedMealResponse DTO
        """
        from src.api.schemas.response.meal_responses import (
            MacrosResponse,
            MealTranslationResponse,
            NutritionOverrideResponse,
            TranslatedFoodItemResponse,
        )

        if not image_url:
            persisted_image = getattr(meal, "image", None)
            if persisted_image is not None:
                image_url = getattr(persisted_image, "url", None)

        # Map food items from nutrition if available
        food_items = []
        total_calories = 0
        total_nutrition = None
        requested_language = (
            normalize_language(target_language) if target_language else None
        )
        display_projections = display_name_by_food_reference or {}
        tracked_food_reference_ids = set(display_projections.keys())
        canonical_dish_name, canonical_food_names = MealMapper._raw_response_canonical(
            meal,
            expected_food_count=(
                len(meal.nutrition.food_items or []) if meal.nutrition else 0
            ),
        )

        if meal.nutrition:
            total_calories = effective_meal_calories(meal)

            # Map total nutrition macros
            if hasattr(meal.nutrition, "macros") and meal.nutrition.macros:
                effective_macros = meal.nutrition.effective_macros
                total_nutrition = MacrosResponse(
                    protein=effective_macros.protein,
                    carbs=effective_macros.carbs,
                    fat=effective_macros.fat,
                    fiber=effective_macros.fiber,
                    sugar=effective_macros.sugar,
                )
            # Handle legacy structure where nutrition has direct properties
            elif hasattr(meal.nutrition, "protein"):
                total_nutrition = MacrosResponse(
                    protein=meal.nutrition.protein,
                    carbs=meal.nutrition.carbs,
                    fat=meal.nutrition.fat,
                )

            # Map persisted food-item display names and retain canonical aliases.
            if meal.nutrition.food_items:
                for index, item in enumerate(meal.nutrition.food_items):
                    item_food_reference_id = getattr(item, "food_reference_id", None)
                    tracked_projection = (
                        display_projections.get(item_food_reference_id)
                        if item_food_reference_id is not None
                        else None
                    )
                    if tracked_projection is not None:
                        canonical_name = tracked_projection.get("name") or item.name
                        display_name = resolve_food_reference_display_name(
                            tracked_projection, requested_language
                        )
                    else:
                        canonical_name = _snapshot_canonical_name(item) or (
                            canonical_food_names[index]
                            if index < len(canonical_food_names)
                            else item.name
                        )
                        display_name = item.name
                    item_calories = effective_food_item_calories(
                        item,
                        meal_source=meal.source,
                        food_label_metadata=meal.food_label_metadata,
                    )
                    nutrition_dto = None
                    if hasattr(item, "macros") and item.macros:
                        nutrition_dto = NutritionResponse(
                            nutrition_id=str(canonical_name),
                            # Use effective item calories so label/overrides
                            # are not replaced by macro-derived totals.
                            calories=item_calories,
                            protein_g=item.macros.protein,
                            carbs_g=item.macros.carbs,
                            fat_g=item.macros.fat,
                            fiber_g=item.macros.fiber,
                            sugar_g=item.macros.sugar,
                        )

                    custom_nutrition_dto = (
                        MealMapper._custom_nutrition_response_for_item(
                            item,
                            item_calories,
                            canonical_name,
                        )
                    )

                    source_nutrition_dto = MealMapper._source_nutrition_response(
                        source_nutrition_by_food_reference,
                        getattr(item, "food_reference_id", None),
                        getattr(item, "source_snapshot", None),
                    )
                    food_item_dto = FoodItemResponse(
                        id=str(item.id),
                        name=display_name,
                        display_name=display_name,
                        canonical_name=canonical_name,
                        name_vi=(
                            tracked_projection.get("name_vi")
                            if tracked_projection is not None
                            else None
                        ),
                        category=None,
                        quantity=item.quantity,
                        unit=item.unit,
                        description=None,
                        nutrition=nutrition_dto,
                        custom_nutrition=custom_nutrition_dto,
                        source_nutrition=source_nutrition_dto,
                        nutrition_override=(
                            NutritionOverrideResponse(
                                calories=item.nutrition_override.calories,
                                protein=item.nutrition_override.protein,
                                carbs=item.nutrition_override.carbs,
                                fat=item.nutrition_override.fat,
                            )
                            if item.nutrition_override
                            else None
                        ),
                        fdc_id=getattr(item, "fdc_id", None),
                        food_reference_id=getattr(item, "food_reference_id", None),
                        is_custom=getattr(item, "is_custom", False),
                        origin=getattr(item, "source_kind", None),
                        source_namespace=(
                            (getattr(item, "source_snapshot", None) or {}).get(
                                "source_namespace"
                            )
                        ),
                        source_food_id=getattr(item, "source_food_id", None),
                        nutrition_contract_version=getattr(
                            item, "nutrition_contract_version", None
                        ),
                        source_snapshot=getattr(item, "source_snapshot", None),
                        allowed_units=overlay_serving_labels(
                            getattr(item, "allowed_units", None) or [],
                            (tracked_projection or {}).get("serving_labels") or {},
                            language=requested_language,
                        )
                        if requested_language == "vi"
                        else (getattr(item, "allowed_units", None) or []),
                    )
                    food_items.append(food_item_dto)

        # Stored names are authoritative for scanner meals. The requested
        # locale must not rewrite those names later.
        dish_name = meal.dish_name
        persisted_image_names = MealMapper.has_persisted_image_display_names(meal)
        translation_language = (
            MealMapper._raw_response_localization_language(meal)
            if persisted_image_names
            else None
        )
        if (
            not persisted_image_names
            and requested_language == "en"
            and canonical_dish_name
        ):
            dish_name = canonical_dish_name
        instructions = MealMapper._normalize_instructions(
            getattr(meal, "instructions", None)
        )
        if not persisted_image_names:
            direct_localization = MealMapper._raw_response_localization(
                meal,
                requested_language,
                expected_food_count=len(food_items),
            )
        else:
            direct_localization = None

        if not persisted_image_names and requested_language == "en":
            for food_item, canonical_name in zip(
                food_items,
                canonical_food_names,
                strict=False,
            ):
                if food_item.food_reference_id in tracked_food_reference_ids:
                    continue
                food_item.name = canonical_name
                food_item.display_name = canonical_name
        elif not persisted_image_names and direct_localization:
            translation_language = direct_localization.language
            dish_name = direct_localization.dish_name
            for food_item, localized_name in zip(
                food_items,
                direct_localization.food_item_names,
                strict=True,
            ):
                if food_item.food_reference_id in tracked_food_reference_ids:
                    continue
                food_item.name = localized_name
                food_item.display_name = localized_name
        elif (
            not persisted_image_names
            and requested_language
            and requested_language != "en"
            and meal.translations
        ):
            tr = meal.translations.get(requested_language)
            if tr and tr.translation_version == CURRENT_MEAL_TRANSLATION_VERSION:
                translation_language = requested_language
                # Apply each translated field independently if it exists
                # (lenient check - scanned meals may not have instructions)
                if tr.dish_name and not keep_stored_display_name(
                    stored=dish_name,
                    translated=tr.dish_name,
                    language=requested_language,
                ):
                    dish_name = tr.dish_name
                if tr.meal_instruction:
                    instructions = tr.meal_instruction
                translated_names_by_id = {
                    str(item.food_item_id): item.name
                    for item in tr.food_items
                    if item.name
                }
                legacy_names_by_id = {}
                if tr.meal_ingredients and len(tr.meal_ingredients) == len(food_items):
                    legacy_names_by_id = {
                        str(fi.id): tr.meal_ingredients[index]
                        for index, fi in enumerate(food_items)
                        if tr.meal_ingredients[index]
                    }
                if translated_names_by_id:
                    for fi in food_items:
                        if fi.food_reference_id in tracked_food_reference_ids:
                            continue
                        translated_name = translated_names_by_id.get(
                            str(fi.id)
                        ) or legacy_names_by_id.get(str(fi.id))
                        _apply_translated_food_name(
                            fi, translated_name, requested_language
                        )
                elif legacy_names_by_id:
                    for i, fi in enumerate(food_items):
                        if fi.food_reference_id in tracked_food_reference_ids:
                            continue
                        _apply_translated_food_name(
                            fi, tr.meal_ingredients[i], requested_language
                        )

        value_insights_response = MealMapper._value_insights_response(value_insights)

        # --- Build translations dict for the response ---
        translations_response = None
        if meal.translations:
            translations_response = {}
            for lang, tr in meal.translations.items():
                translations_response[lang] = MealTranslationResponse(
                    language=tr.language,
                    dish_name=tr.dish_name,
                    meal_instruction=tr.meal_instruction,
                    meal_ingredients=tr.meal_ingredients,
                    food_items=[
                        TranslatedFoodItemResponse(
                            id=fi.food_item_id,
                            name=fi.name,
                            description=fi.description,
                        )
                        for fi in tr.food_items
                    ],
                    translated_at=tr.translated_at,
                )

        return DetailedMealResponse(
            meal_id=meal.meal_id,
            status=STATUS_MAPPING.get(meal.status.value, meal.status.value.lower()),
            dish_name=dish_name,
            emoji=meal.emoji,
            meal_type=meal.meal_type,
            source=meal.source,
            ready_at=meal.ready_at,
            error_message=meal.error_message,
            created_at=meal.created_at,
            updated_at=getattr(meal, "updated_at", None),
            food_items=food_items,
            image_url=image_url,
            total_calories=total_calories,
            total_weight_grams=(
                meal.weight_grams if hasattr(meal, "weight_grams") else None
            ),
            total_nutrition=total_nutrition,
            nutrition_override=(
                NutritionOverrideResponse(
                    calories=meal.nutrition.nutrition_override.calories,
                    protein=meal.nutrition.nutrition_override.protein,
                    carbs=meal.nutrition.nutrition_override.carbs,
                    fat=meal.nutrition.nutrition_override.fat,
                )
                if meal.nutrition and meal.nutrition.nutrition_override
                else None
            ),
            translations=translations_response,
            food_label_metadata=MealMapper._food_label_metadata(meal),
            value_insights=value_insights_response,
            translation_language=translation_language,
            description=getattr(meal, "description", None),
            instructions=instructions,
            prep_time_min=getattr(meal, "prep_time_min", None),
            cook_time_min=getattr(meal, "cook_time_min", None),
            cuisine_type=getattr(meal, "cuisine_type", None),
            origin_country=getattr(meal, "origin_country", None),
        )

    @staticmethod
    def _raw_response_localization(
        meal: Meal,
        target_language: str | None,
        *,
        expected_food_count: int,
    ) -> MealResponseLocalization | None:
        """Read validated same-call display fields from the stored AI payload."""
        language = normalize_language(target_language)
        if language == "en" or not meal.raw_gpt_json:
            return None
        try:
            structured_data = json.loads(meal.raw_gpt_json)
            return parse_meal_response_localization(
                structured_data,
                language,
                expected_food_count=expected_food_count,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _raw_response_canonical(
        meal: Meal,
        *,
        expected_food_count: int,
    ) -> tuple[str | None, tuple[str, ...]]:
        """Read canonical English names retained in the raw analysis payload."""
        if not meal.raw_gpt_json:
            return None, ()
        try:
            structured_data = json.loads(meal.raw_gpt_json)
        except (TypeError, ValueError):
            return None, ()
        if not isinstance(structured_data, dict):
            return None, ()

        dish_name = structured_data.get("dish_name")
        canonical_dish_name = dish_name.strip() if isinstance(dish_name, str) else None
        foods = structured_data.get("foods")
        if not isinstance(foods, list) or len(foods) != expected_food_count:
            return canonical_dish_name, ()

        names: list[str] = []
        for food in foods:
            name = food.get("name") if isinstance(food, dict) else None
            if not isinstance(name, str) or not name.strip():
                return canonical_dish_name, ()
            names.append(name.strip())
        return canonical_dish_name, tuple(names)

    @staticmethod
    def has_direct_response_localization(meal: Meal, language: str) -> bool:
        """Return whether a fresh scan can serve its same-call locale directly."""
        food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
        return (
            MealMapper._raw_response_localization(
                meal,
                language,
                expected_food_count=len(food_items),
            )
            is not None
        )

    @staticmethod
    def has_persisted_image_display_names(meal: Meal) -> bool:
        """Return whether an image meal can trust its stored display names."""
        return getattr(meal, "source", None) in {
            "scanner",
            "food_label",
        } and not getattr(meal, "translations", None)

    @staticmethod
    def _raw_response_localization_language(meal: Meal) -> str | None:
        """Read the locale used for persisted same-call display names."""
        if not meal.raw_gpt_json:
            return None
        try:
            structured_data = json.loads(meal.raw_gpt_json)
        except (TypeError, ValueError):
            return None
        if not isinstance(structured_data, dict):
            return None
        localized_language = structured_data.get("localized_language")
        if not isinstance(localized_language, str):
            return None
        language = normalize_language(localized_language)
        return language if language != "en" else None

    @staticmethod
    def _source_nutrition_response(
        source_nutrition_by_food_reference: dict[int, FoodReferenceNutritionProjection]
        | None,
        food_reference_id: int | None,
        source_snapshot: dict | None = None,
    ):
        if source_snapshot:
            required = (
                source_snapshot.get("protein_per_100g"),
                source_snapshot.get("carbs_per_100g"),
                source_snapshot.get("fat_per_100g"),
            )
            if any(value is None for value in required):
                return None
            return MealMapper._custom_nutrition_response(
                source_snapshot.get("calories_per_100g"),
                source_snapshot.get("protein_per_100g"),
                source_snapshot.get("carbs_per_100g"),
                source_snapshot.get("fat_per_100g"),
                source_snapshot.get("fiber_per_100g", 0),
                source_snapshot.get("sugar_per_100g", 0),
            )

        if not source_nutrition_by_food_reference or food_reference_id is None:
            return None

        reference = source_nutrition_by_food_reference.get(food_reference_id)
        if reference is None or any(
            value is None
            for value in (
                reference.protein_100g,
                reference.carbs_100g,
                reference.fat_100g,
            )
        ):
            return None

        macros = Macros(
            protein=reference.protein_100g,
            carbs=reference.carbs_100g,
            fat=reference.fat_100g,
            fiber=reference.fiber_100g,
            sugar=reference.sugar_100g,
        )
        return MealMapper._custom_nutrition_response(
            macros.total_calories,
            macros.protein,
            macros.carbs,
            macros.fat,
            macros.fiber,
            macros.sugar,
        )

    @staticmethod
    def _custom_nutrition_response_for_item(
        item: FoodItem,
        item_calories: float,
        food_name: str | None = None,
    ):
        from src.api.schemas.response.meal_responses import CustomNutritionResponse

        if not getattr(item, "is_custom", False) or item.quantity <= 0:
            return None

        quantity_grams = quantity_to_grams(
            item.quantity,
            item.unit,
            food_name or item.name,
            getattr(item, "allowed_units", None) or [],
        )
        if quantity_grams <= 0:
            return None
        scale_factor = 100.0 / quantity_grams
        protein = item.macros.protein * scale_factor if item.macros else 0.0
        carbs = item.macros.carbs * scale_factor if item.macros else 0.0
        fat = item.macros.fat * scale_factor if item.macros else 0.0
        fiber = item.macros.fiber * scale_factor if item.macros else 0.0
        sugar = item.macros.sugar * scale_factor if item.macros else 0.0
        derived = Macros(
            protein=protein,
            carbs=carbs,
            fat=fat,
            fiber=fiber,
        ).total_calories
        return CustomNutritionResponse(
            calories_per_100g=reconcile_calories_per_100g(
                item_calories * scale_factor,
                derived,
            ),
            protein_per_100g=protein,
            carbs_per_100g=carbs,
            fat_per_100g=fat,
            fiber_per_100g=fiber,
            sugar_per_100g=sugar,
        )

    @staticmethod
    def _custom_nutrition_response(calories, protein, carbs, fat, fiber, sugar):
        from src.api.schemas.response.meal_responses import CustomNutritionResponse

        return CustomNutritionResponse(
            calories_per_100g=calories
            if calories is not None
            else Macros(
                protein=protein,
                carbs=carbs,
                fat=fat,
                fiber=fiber,
                sugar=sugar,
            ).total_calories,
            protein_per_100g=protein,
            carbs_per_100g=carbs,
            fat_per_100g=fat,
            fiber_per_100g=fiber,
            sugar_per_100g=sugar,
        )

    @staticmethod
    def _value_insights_response(insights: MealValueInsights | None):
        return MealMapper.to_value_insights_response(insights)

    @staticmethod
    def to_value_insights_response(insights: MealValueInsights | None):
        from src.api.schemas.response.meal_responses import (
            IngredientValueInsightResponse,
            MealValueBulletResponse,
            MealValueInsightsResponse,
        )

        if not insights:
            return None
        return MealValueInsightsResponse(
            meal_bullets=[
                MealValueBulletResponse(
                    text=item.text,
                    category=item.category,
                    highlights=item.highlights[:1],
                )
                for item in insights.meal_bullets
            ],
            ingredient_insights=[
                IngredientValueInsightResponse(
                    ingredient_name=item.ingredient_name,
                    text=item.text,
                    category=item.category,
                    highlights=item.highlights[:1],
                )
                for item in insights.ingredient_insights
            ],
        )

    @staticmethod
    def _food_label_metadata(meal: Meal) -> FoodLabelMetadataResponse | None:
        if meal.source != "food_label":
            return None
        metadata = getattr(meal, "food_label_metadata", None)
        if isinstance(metadata, dict):
            response = MealMapper._food_label_metadata_from_dict(metadata)
            if response is not None:
                return response
        if not meal.raw_gpt_json:
            return None
        try:
            data = json.loads(meal.raw_gpt_json)
        except json.JSONDecodeError:
            return None
        return MealMapper._food_label_metadata_from_dict(data)

    @staticmethod
    def _food_label_metadata_from_dict(
        data: dict[str, Any],
    ) -> FoodLabelMetadataResponse | None:
        try:
            serving_size = data.get("serving_size") or {}
            return FoodLabelMetadataResponse(
                product_name=data["product_name"],
                brand=data.get("brand"),
                serving_size=FoodLabelServingSizeResponse(
                    display_text=serving_size["display_text"],
                    grams=float(serving_size["grams"]),
                ),
                servings_per_package=float(data["servings_per_package"]),
                label_calories_per_serving=data.get("label_calories_per_serving"),
                confidence=float(data.get("confidence", 0.5)),
                label_notes=list(data.get("label_notes") or []),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_instructions(instructions: list | None) -> list | None:
        """Normalize instructions to structured format.

        Converts legacy List[str] to List[dict] with {instruction, duration_minutes}.
        Already-structured dicts are passed through unchanged.
        """
        if not instructions:
            return None
        result = []
        for item in instructions:
            if isinstance(item, str):
                result.append({"instruction": item, "duration_minutes": None})
            elif isinstance(item, dict):
                result.append(item)
        return result if result else None

    @staticmethod
    def to_meal_list_response(
        meals: list[Meal],
        total: int,
        page: int = 1,
        page_size: int = 10,
        image_urls: dict | None = None,
    ) -> MealListResponse:
        """
        Convert list of Meal domain models to MealListResponse DTO.

        Args:
            meals: List of Meal domain models
            total: Total count of meals
            page: Current page number
            page_size: Items per page
            image_urls: Optional dict mapping meal_id to image URLs

        Returns:
            MealListResponse DTO
        """
        image_urls = image_urls or {}

        meal_responses = []
        for meal in meals:
            if meal.nutrition and meal.nutrition.food_items:  # Has detailed info
                response = MealMapper.to_detailed_response(
                    meal, image_urls.get(meal.meal_id)
                )
            else:  # Simple response
                response = MealMapper.to_simple_response(meal)
            meal_responses.append(response)

        return MealListResponse(
            meals=meal_responses,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size,
        )

    @staticmethod
    def map_nutrition_from_dict(nutrition_dict: dict) -> Nutrition:
        """
        Create Nutrition domain model from dictionary.

        Args:
            nutrition_dict: Dictionary with nutrition data

        Returns:
            Nutrition domain model
        """
        macros = Macros(
            protein=nutrition_dict.get("protein_g", 0),
            carbs=nutrition_dict.get("carbs_g", 0),
            fat=nutrition_dict.get("fat_g", 0),
        )

        micros = None
        if "sodium_mg" in nutrition_dict:
            micros = Micros(sodium=nutrition_dict.get("sodium_mg", 0))

        return Nutrition(macros=macros, micros=micros, food_items=[])

    @staticmethod
    def map_food_item_from_dict(item_dict: dict) -> FoodItem:
        """
        Create FoodItem domain model from dictionary.

        Args:
            item_dict: Dictionary with food item data

        Returns:
            FoodItem domain model
        """
        # Extract macros from nutrition dict if present; calories are derived.
        macros = Macros(protein=0, carbs=0, fat=0)
        micros = None

        if "nutrition" in item_dict and item_dict["nutrition"]:
            nutrition_data = item_dict["nutrition"]
            macros = Macros(
                protein=nutrition_data.get("protein_g", 0),
                carbs=nutrition_data.get("carbs_g", 0),
                fat=nutrition_data.get("fat_g", 0),
            )
            if "sodium_mg" in nutrition_data:
                micros = Micros(sodium=nutrition_data.get("sodium_mg", 0))

        return FoodItem(
            id=item_dict.get("id", ""),
            name=item_dict.get("name", ""),
            quantity=item_dict.get("quantity", 0),
            unit=item_dict.get("unit", ""),
            macros=macros,
            micros=micros,
            confidence=item_dict.get("confidence", 1.0),
            fdc_id=item_dict.get("fdc_id"),
            food_reference_id=item_dict.get("food_reference_id"),
            is_custom=item_dict.get("is_custom", False),
            allowed_units=item_dict.get("allowed_units") or None,
            source_kind=item_dict.get("origin") or item_dict.get("source_kind"),
            source_food_id=item_dict.get("source_food_id"),
            nutrition_contract_version=item_dict.get("nutrition_contract_version"),
            source_snapshot=item_dict.get("source_snapshot"),
        )

    @staticmethod
    def to_daily_nutrition_response(daily_macros_data: dict) -> DailyNutritionResponse:
        """
        Convert daily macros query result to DailyNutritionResponse DTO.

        Args:
            daily_macros_data: Dictionary with daily macros data from query

        Returns:
            DailyNutritionResponse DTO
        """
        from src.api.exceptions import ResourceNotFoundException
        from src.api.schemas.response.daily_nutrition_response import (
            HydrationSummaryResponse,
            MacrosResponse,
            WeeklyContextResponse,
        )

        # Extract data - require actual user targets, no hardcoded defaults
        target_calories = daily_macros_data.get("target_calories")
        if not target_calories:
            raise ResourceNotFoundException(
                message="User profile not found or incomplete. Please complete onboarding first.",
                error_code="TDEE_DATA_NOT_FOUND",
                details={
                    "user_id": daily_macros_data.get("user_id"),
                    "reason": "User has not completed onboarding or TDEE calculation is missing",
                },
            )

        target_macros_data = daily_macros_data.get("target_macros") or {}
        if not isinstance(target_macros_data, dict):
            target_macros_data = {}
        target_macros = MacrosResponse(
            protein=target_macros_data.get("protein") or 0.0,
            carbs=target_macros_data.get("carbs") or 0.0,
            fat=target_macros_data.get("fat") or 0.0,
        )

        consumed_macros = MacrosResponse(
            protein=daily_macros_data.get("total_protein", 0.0),
            carbs=daily_macros_data.get("total_carbs", 0.0),
            fat=daily_macros_data.get("total_fat", 0.0),
        )

        consumed_calories = daily_macros_data.get("total_calories", 0.0)

        # Calculate remaining macros
        remaining_calories = max(0, target_calories - consumed_calories)
        remaining_macros = MacrosResponse(
            protein=max(0, target_macros.protein - consumed_macros.protein),
            carbs=max(0, target_macros.carbs - consumed_macros.carbs),
            fat=max(0, target_macros.fat - consumed_macros.fat),
        )

        # Calculate completion percentages
        completion_percentage = {
            "calories": (
                (consumed_calories / target_calories * 100)
                if target_calories > 0
                else 0
            ),
            "protein": (
                (consumed_macros.protein / target_macros.protein * 100)
                if target_macros.protein > 0
                else 0
            ),
            "carbs": (
                (consumed_macros.carbs / target_macros.carbs * 100)
                if target_macros.carbs > 0
                else 0
            ),
            "fat": (
                (consumed_macros.fat / target_macros.fat * 100)
                if target_macros.fat > 0
                else 0
            ),
        }

        # Parse weekly context if present
        weekly_context = None

        if daily_macros_data.get("weekly_context"):
            wc = daily_macros_data["weekly_context"]
            weekly_context = WeeklyContextResponse(
                adjusted_target_calories=wc.get(
                    "adjusted_target_calories", target_calories
                ),
                adjusted_target_carbs=wc.get(
                    "adjusted_target_carbs", target_macros.carbs
                ),
                adjusted_target_fat=wc.get("adjusted_target_fat", target_macros.fat),
                daily_protein=wc.get("daily_protein", target_macros.protein),
                bmr_floor_active=wc.get("bmr_floor_active", False),
                remaining_days=wc.get("remaining_days", 7),
            )

        # Parse hydration summary if present
        hydration_data = daily_macros_data.get("hydration")
        hydration = None
        if hydration_data:
            hydration = HydrationSummaryResponse(
                consumed_ml=hydration_data.get("consumed_ml", 0),
                goal_ml=hydration_data.get("goal_ml", 2000),
                percentage=hydration_data.get("percentage", 0.0),
            )

        return DailyNutritionResponse(
            date=daily_macros_data.get("date", ""),
            target_calories=target_calories,
            target_macros=target_macros,
            consumed_calories=consumed_calories,
            # Gross intake + burn, so burn-owning clients avoid double-subtraction.
            food_calories=daily_macros_data.get("food_calories"),
            movement_kcal_burned=daily_macros_data.get("movement_kcal_burned", 0.0),
            consumed_macros=consumed_macros,
            remaining_calories=remaining_calories,
            remaining_macros=remaining_macros,
            completion_percentage=completion_percentage,
            weekly_context=weekly_context,
            hydration=hydration,
        )
