from decimal import Decimal

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.services.meal_recommendation.catalog_ingredient_statistics_service import (
    CatalogIngredientStatisticsService,
)
from src.domain.services.meal_recommendation.plan_diversity_reranking_service import (
    SHORTLIST_LIMIT,
    PlanDiversityRerankingService,
)
from src.domain.services.meal_recommendation.recipe_scoring_service import RecipeScore


def test_weighted_overlap_is_bounded_symmetric_and_rare_sensitive():
    stats = CatalogIngredientStatisticsService().build(
        [
            _meal("common-1", (1, 2)),
            _meal("common-2", (1, 3)),
            _meal("rare", (1, 4)),
        ]
    )
    service = PlanDiversityRerankingService()

    common_overlap = service.weighted_overlap(
        _meal("candidate", (1, 2)),
        _meal("comparison", (1, 3)),
        stats,
    )
    rare_overlap = service.weighted_overlap(
        _meal("candidate", (1, 4)),
        _meal("comparison", (1, 4)),
        stats,
    )

    assert 0 <= common_overlap <= 1
    assert rare_overlap > common_overlap
    assert service.weighted_overlap(_meal("a", (2,)), _meal("b", (3,)), stats) == 0
    assert service.weighted_overlap(_meal("x", (1, 2)), _meal("y", (1,)), stats) == (
        service.weighted_overlap(_meal("y", (1,)), _meal("x", (1, 2)), stats)
    )


def test_contextual_rerank_caps_work_to_top_30_candidates():
    stats = CatalogIngredientStatisticsService().build(
        [_meal(f"meal-{index:02d}", (index + 1,)) for index in range(40)]
    )
    comparisons = (_meal("selected", (99,)),)
    calls = 0

    def diversity_fit(candidate, comparison_meals, statistics):
        nonlocal calls
        calls += 1
        return 1.0

    service = PlanDiversityRerankingService(diversity_fit=diversity_fit)
    ranked = [
        RecipeScore(_meal(f"meal-{index:02d}", (index + 1,)), 1 - index / 100)
        for index in range(40)
    ]

    result = service.rerank_shortlist(
        ranked,
        comparison_meals=comparisons,
        ingredient_statistics=stats,
    )

    assert len(result) == SHORTLIST_LIMIT
    assert calls == SHORTLIST_LIMIT
    assert {item.catalog_meal.id for item in result}.isdisjoint({"meal-30", "meal-31"})


def test_weighted_overlap_ignores_unmapped_ingredients():
    stats = CatalogIngredientStatisticsService().build(
        [_meal("mapped", (1,)), _meal("unmapped", (None,))]
    )

    overlap = PlanDiversityRerankingService().weighted_overlap(
        _meal("candidate", (None, 1)),
        _meal("comparison", (1,)),
        stats,
    )

    assert overlap == 1.0


def _meal(meal_id: str, food_reference_ids: tuple[int | None, ...]) -> CatalogMeal:
    return CatalogMeal(
        id=meal_id,
        catalog_key=f"key-{meal_id}",
        content_hash=f"{meal_id:0<64}"[:64],
        name=f"Meal {meal_id}",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("20"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast",),
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=food_reference_id,
                display_name="Ingredient",
                quantity=Decimal("100"),
                unit="g",
            )
            for food_reference_id in food_reference_ids
        ),
    )
