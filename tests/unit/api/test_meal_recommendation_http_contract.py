from datetime import date
from decimal import Decimal

from src.api.routes.v1.meal_recommendation_route_support import to_response
from src.api.schemas.response.meal_recommendation_responses import (
    MealRecommendationPlanResponse,
    MealRecommendationPlanSummaryResponse,
)
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    CatalogMealIngredient,
    PersistedMealRecommendationCandidate,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
)


def test_current_response_contract_is_full_plan_with_hydrated_candidates():
    response = to_response(_full_plan())
    payload = response.model_dump(mode="json")

    assert response.id == "plan-baseline"
    assert response.allergy_evaluated is False
    assert len(response.slots) == 9
    assert sum(len(slot.alternatives) for slot in response.slots) == 45
    assert response.slots[0].catalog_meal.ingredients[0].display_name == "Rice"
    assert response.slots[0].alternatives[0].catalog_meal.ingredients[0].unit == "g"
    assert _json_size(response) == 28465
    assert "alternatives" in payload["slots"][0]
    assert "ingredients" in payload["slots"][0]["catalog_meal"]


def test_current_openapi_schema_exposes_full_plan_fields():
    schema = MealRecommendationPlanResponse.model_json_schema()
    definitions = schema["$defs"]

    slot_fields = definitions["MealRecommendationSlotResponse"]["properties"]
    meal_fields = definitions["MealRecommendationCatalogMealResponse"]["properties"]

    assert "alternatives" in slot_fields
    assert "catalog_meal" in slot_fields
    assert "ingredients" in meal_fields
    assert "macros" in meal_fields


def test_summary_openapi_schema_keeps_selected_ingredients_compact():
    schema = MealRecommendationPlanSummaryResponse.model_json_schema()
    definitions = schema["$defs"]

    slot_fields = definitions["MealRecommendationSlotSummaryResponse"]["properties"]
    meal_fields = definitions["MealRecommendationCatalogMealSummaryResponse"][
        "properties"
    ]

    assert "alternatives" not in slot_fields
    assert "score" not in slot_fields
    assert "ingredients" in meal_fields
    assert "description" not in meal_fields
    assert "macros" in meal_fields


def _json_size(response: MealRecommendationPlanResponse) -> int:
    return len(response.model_dump_json().encode("utf-8"))


def _full_plan() -> PersistedMealRecommendationPlan:
    slots = []
    for day_index in range(3):
        for position, meal_type in enumerate(("breakfast", "lunch", "dinner")):
            slot_number = day_index * 3 + position
            slot_id = f"slot-{slot_number}"
            selected_meal = _catalog_meal(
                f"{meal_type}-selected-{day_index}", meal_type
            )
            selected = PersistedMealRecommendationCandidate(
                id="plan-baseline" if slot_number == 0 else f"selected-{slot_number}",
                slot_id=slot_id,
                recommendation_date=date(2026, 7, 20 + day_index),
                meal_type=meal_type,
                catalog_meal_id=selected_meal.id,
                candidate_rank=0,
                is_selected=True,
                score=Decimal("0.82"),
                selection_version=1,
                catalog_meal=selected_meal,
            )
            alternatives = tuple(
                PersistedMealRecommendationCandidate(
                    id=f"alternative-{slot_number}-{rank}",
                    slot_id=slot_id,
                    recommendation_date=date(2026, 7, 20 + day_index),
                    meal_type=meal_type,
                    catalog_meal_id=f"{meal_type}-alternative-{day_index}-{rank}",
                    candidate_rank=rank,
                    is_selected=False,
                    score=Decimal(f"0.8{rank}"),
                    selection_version=1,
                    catalog_meal=_catalog_meal(
                        f"{meal_type}-alternative-{day_index}-{rank}", meal_type
                    ),
                )
                for rank in range(1, 6)
            )
            slots.append(
                PersistedMealRecommendationSlot(
                    id=slot_id,
                    slot_date=date(2026, 7, 20 + day_index),
                    day_index=day_index,
                    meal_type=meal_type,
                    catalog_meal_id=selected_meal.id,
                    target_calories=500 if meal_type == "breakfast" else 750,
                    score=0.82,
                    position=slot_number,
                    selected=selected,
                    alternatives=alternatives,
                )
            )
    return PersistedMealRecommendationPlan(
        id="plan-baseline",
        user_id="user-1",
        status="active",
        timezone="Asia/Ho_Chi_Minh",
        start_date=date(2026, 7, 20),
        daily_calories=2000,
        operation="three_day",
        idempotency_key="key-1",
        request_fingerprint="f" * 64,
        slots=tuple(slots),
    )


def _catalog_meal(catalog_meal_id: str, meal_type: str) -> CatalogMeal:
    return CatalogMeal(
        id=catalog_meal_id,
        catalog_key=f"key-{catalog_meal_id}",
        content_hash=f"{catalog_meal_id:0<64}"[:64],
        name=f"{meal_type.title()} Meal {catalog_meal_id}",
        cuisine="vietnamese",
        description="Baseline full response description",
        image_url="https://example.com/meal.jpg",
        protein_g=Decimal("25"),
        carbs_g=Decimal("50"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        sugar_g=Decimal("4"),
        meal_types=(meal_type,),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=7,
                display_name="Rice",
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )
