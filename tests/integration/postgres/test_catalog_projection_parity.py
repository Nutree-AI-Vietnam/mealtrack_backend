"""Real PostgreSQL parity and fencing for rebuildable catalog projections."""

from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text

from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.ports.catalog_recipe_repository_port import (
    CatalogMealRepositoryPort,
    CatalogRecipePage,
)
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    resolve_allergen_preferences,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    WeeklyPlanGenerationService,
)
from src.infra.database.models.meal_recommendation import MealCatalogIngredientORM
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM,
)
from src.infra.repositories.catalog_projection_legacy_page import legacy_recipe_page
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,
)
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)

pytestmark = pytest.mark.integration


from tests.integration.postgres.catalog_projection_fixtures import seed_catalog


async def _authoritative_page(repo, *, allergies=(), **options):
    """Canonical-table page used as the projection fallback."""
    known = await repo.list_allergen_codes() if allergies else ()
    codes = resolve_allergen_preferences(allergies, known)
    if codes is None:
        return CatalogRecipePage((), 0)
    page = {
        "query": None,
        "diet": None,
        "max_cook_time": None,
        "cuisine": None,
        "meal_type": None,
        "dislikes": (),
        **options,
    }
    return await legacy_recipe_page(repo, codes=codes, **page)


@pytest.mark.asyncio
async def test_projected_filter_order_count_and_compact_generation_match_authority(
    pg_session,
):
    ids, _ = await seed_catalog(pg_session)
    projected = AsyncCatalogMealRepository(pg_session)
    filters = [
        {},
        {"query": "STRASSE"},
        {"query": "%_"},
        {"cuisine": "STRASSE"},
        {"query": "İSTANBUL"},
        {"query": "σίσυφοσ"},
        {"diet": "vegetarian"},
        {"diet": "no-pork"},
        {"diet": "unsupported"},
        {"max_cook_time": 20},
        {"meal_type": "lunch"},
        {"meal_type": "dinner"},
        {"dislikes": ("CÁ", "  ")},
        {"allergies": ("dairy",)},
        {"allergies": ("unknown-code",)},
        {"min_protein": 0},
        {"min_protein": 4},
        {"min_protein": 1000},
        {"diet": "vegetarian", "min_protein": 4},
    ]
    for options in filters:
        for offset in (0, 1, 99):
            before = await _authoritative_page(
                projected, **options, limit=2, offset=offset
            )
            after = await projected.list_recipe_page(**options, limit=2, offset=offset)
            assert (after.total, [meal.id for meal in after.items]) == (
                before.total,
                [meal.id for meal in before.items],
            )
            assert all(
                not meal.ingredients and meal.recipe_payload is None
                for meal in after.items
            )
            assert [
                (meal.protein_g, meal.carbs_g, meal.calories) for meal in after.items
            ] == [
                (meal.protein_g, meal.carbs_g, meal.calories) for meal in before.items
            ]
            # Labels come from the projection columns the diet filter reads.
            assert [meal.diet_labels for meal in after.items] == [
                meal.diet_labels for meal in before.items
            ]
            assert all(meal.browse_labels is not None for meal in after.items)
    meals = await projected.list_active_meals()
    candidates = await projected.list_selection_candidates()
    assert {meal.id for meal in candidates} == set(ids)
    assert all(not meal.ingredients and meal.selection_features for meal in candidates)
    generator = WeeklyPlanGenerationService()
    for preferences in [
        WeeklyMealPlanPreferences(),
        WeeklyMealPlanPreferences(diet="vegetarian"),
        WeeklyMealPlanPreferences(diet="no-pork"),
        WeeklyMealPlanPreferences(allergies=("milk",)),
        WeeklyMealPlanPreferences(dislikes=("Cá",)),
        WeeklyMealPlanPreferences(cooking_time="30"),
    ]:
        args = {
            "user_id": "synthetic",
            "week_start_date": "2026-09-28",
            "daily_calories": 2000,
            "preferences": preferences,
        }
        assert generator.generate(meals, **args) == generator.generate(
            candidates, **args
        )


@pytest.mark.asyncio
async def test_selection_ingredient_lines_match_active_meal_ingredients(pg_session):
    ids, _ = await seed_catalog(pg_session)
    pg_session.add(
        MealCatalogIngredientORM(
            catalog_meal_id=ids[0],
            position=2,
            food_reference_id=None,
            display_name="Muối",
            quantity=Decimal("2"),
            unit="g",
            is_optional=True,
        )
    )
    await pg_session.execute(
        text("UPDATE meal_catalog SET is_active = false WHERE id = :id"),
        {"id": ids[1]},
    )
    await pg_session.commit()
    repo = AsyncCatalogMealRepository(pg_session)

    lines = await repo.list_selection_ingredient_lines()

    authority = await CatalogMealRepositoryPort.list_selection_ingredient_lines(repo)
    assert Counter(lines) == Counter(authority)
    assert (ids[0], None, "Muối") in lines
    assert ids[1] not in {recipe_id for recipe_id, _, _ in lines}


@pytest.mark.asyncio
async def test_breakfast_browse_excludes_non_meal_titles_on_every_path(pg_session):
    ids, _ = await seed_catalog(pg_session)
    await pg_session.execute(text("UPDATE meal_catalog SET breakfast_eligible = true"))
    await pg_session.commit()
    projected = AsyncCatalogMealRepository(pg_session)

    dirty = await projected.list_recipe_page(meal_type="breakfast", limit=20)
    await CatalogProjectionRebuilder(pg_session).rebuild(tuple(ids))
    clean = await projected.list_recipe_page(meal_type="breakfast", limit=20)

    assert not dirty.projected and clean.projected
    for page in (dirty, clean):
        names = [meal.name for meal in page.items]
        assert "Tofu pudding" not in names
        assert page.total == len(ids) - 1


@pytest.mark.asyncio
async def test_dirty_nutrition_retains_sql_totals_and_only_hydrates_page_ids(
    pg_session,
):
    ids, food_id = await seed_catalog(pg_session)
    repo = AsyncCatalogMealRepository(pg_session)
    version = await repo.capture_catalog_publication_version()
    await pg_session.execute(
        text(
            "UPDATE food_reference SET extra_nutrients = CAST(:micros AS json) WHERE id=:id"
        ),
        {"id": food_id, "micros": '{"iron_mg":2}'},
    )
    micros_version = await repo.capture_catalog_publication_version()
    assert micros_version.selection == version.selection
    assert micros_version.enrichment > version.enrichment
    await pg_session.execute(
        text("UPDATE food_reference SET protein_100g=9 WHERE id=:id"), {"id": food_id}
    )
    await pg_session.commit()
    loaded = []
    original = repo.get_meals

    async def capture(selected):
        loaded.extend(selected)
        return await original(selected)

    repo.get_meals = capture
    page = await repo.list_recipe_page(limit=2)
    assert page.total == len(ids) and page.projected
    assert loaded == [meal.id for meal in page.items]
    assert all(meal.protein_g == Decimal("9") for meal in page.items)
    rows = (await pg_session.execute(select(MealCatalogProjectionORM))).scalars().all()
    assert all(
        row.nutrition_dirty and not row.query_dirty and row.enrichment_dirty
        for row in rows
    )


@pytest.mark.asyncio
async def test_compact_summaries_use_one_select_and_backfill_is_restartable(pg_session):
    ids, _ = await seed_catalog(pg_session)
    repo = AsyncCatalogMealRepository(pg_session)
    statements = []

    def record(_connection, _cursor, sql, _params, _context, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    engine = pg_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert len(await repo.get_meal_summaries(ids)) == len(ids)
        assert len(statements) == 1
        assert "food_reference" not in statements[0]
    finally:
        event.remove(engine, "before_cursor_execute", record)
    rebuilder = CatalogProjectionRebuilder(pg_session)
    count, cursor = await rebuilder.reconcile_page(limit=2)
    assert count == 2 and cursor
    count2, cursor2 = await rebuilder.reconcile_page(after_id=cursor, limit=2)
    assert count2 == 2 and cursor2 > cursor


@pytest.mark.asyncio
async def test_batch_details_load_active_recipes_with_steps(pg_session):
    ids, _ = await seed_catalog(pg_session)
    repo = AsyncCatalogMealRepository(pg_session)

    details = await repo.get_meal_details([ids[1], "missing", ids[0], ids[1]])

    assert sorted(meal.id for meal in details) == sorted(ids[:2])
    single = await repo.get_meal_detail(ids[0])
    assert next(meal for meal in details if meal.id == ids[0]) == single
