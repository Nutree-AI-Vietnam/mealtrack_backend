"""Real PostgreSQL parity and fencing for rebuildable catalog projections."""

import asyncio

import pytest
from sqlalchemy import select, text

from src.infra.database.models.meal_recommendation import MealCatalogStepORM
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM,
)
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,
)
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)

pytestmark = pytest.mark.integration


from tests.integration.postgres.catalog_projection_fixtures import seed_catalog


@pytest.mark.asyncio
async def test_dirty_query_falls_back_and_withdrawal_is_immediate(pg_session):
    ids, _ = await seed_catalog(pg_session)
    repo = AsyncCatalogMealRepository(pg_session)
    await pg_session.execute(
        text("UPDATE meal_catalog SET name='Edited bowl' WHERE id=:id"), {"id": ids[0]}
    )
    await pg_session.commit()
    page = await repo.list_recipe_page(query="Edited")
    assert not page.projected and [meal.id for meal in page.items] == [ids[0]]
    await pg_session.execute(
        text("UPDATE meal_catalog SET is_active=false WHERE id=:id"), {"id": ids[0]}
    )
    await pg_session.commit()
    assert await repo.get_meal_summaries([ids[0]]) == []
    assert not (await repo.list_recipe_page(query="Edited")).items


@pytest.mark.asyncio
async def test_shared_publication_fence_blocks_direct_sql_writer(
    pg_session, async_session_factory
):
    ids, _ = await seed_catalog(pg_session)
    async with async_session_factory() as reader, async_session_factory() as writer:
        repo = AsyncCatalogMealRepository(reader)
        captured = await repo.lock_catalog_publication(shared=True)
        begun = asyncio.Event()

        async def publish():
            begun.set()
            await writer.execute(
                text("UPDATE meal_catalog SET is_active=false WHERE id=:id"),
                {"id": ids[0]},
            )
            await writer.commit()

        task = asyncio.create_task(publish())
        await begun.wait()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert await repo.capture_catalog_publication_version() == captured
        await reader.rollback()
        await asyncio.wait_for(task, timeout=3)
        assert (
            await repo.capture_catalog_publication_version()
        ).selection > captured.selection


@pytest.mark.asyncio
async def test_reference_rows_without_recipes_leave_epochs_and_jobs_unchanged(
    pg_session,
):
    ids, food_id = await seed_catalog(pg_session)
    repo = AsyncCatalogMealRepository(pg_session)

    async def jobs():
        return (
            await pg_session.execute(
                text("SELECT count(*) FROM catalog_preparation_jobs")
            )
        ).scalar_one()

    before, jobs_before = await repo.capture_catalog_publication_version(), await jobs()
    await pg_session.execute(
        text(
            "INSERT INTO food_reference (name, name_normalized, protein_100g, carbs_100g,"
            " fat_100g, fiber_100g, sugar_100g, source, region, is_verified, density)"
            " VALUES ('Scanned snack', 'scanned snack', 1, 2, 3, 0, 0, 'fatsecret',"
            " 'global', false, 1)"
        )
    )
    await pg_session.execute(
        text("UPDATE food_reference SET protein_100g = 4 WHERE name = 'Scanned snack'")
    )
    assert await repo.capture_catalog_publication_version() == before
    assert await jobs() == jobs_before

    await pg_session.execute(
        text("UPDATE food_reference SET protein_100g = 9 WHERE id = :id"),
        {"id": food_id},
    )
    after = await repo.capture_catalog_publication_version()
    assert after.selection > before.selection
    assert await jobs() == jobs_before + len(ids)


@pytest.mark.asyncio
async def test_rebuilt_facets_cover_micros_titles_and_normalized_steps(pg_session):
    ids, food_id = await seed_catalog(pg_session)

    async def facets():
        row = (
            await pg_session.execute(
                select(MealCatalogProjectionORM)
                .where(MealCatalogProjectionORM.catalog_meal_id == ids[0])
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return (
            row.selection_digest,
            row.ingredient_digest,
            row.translation_digest,
            row.enrichment_digest,
        )

    original = await facets()
    await pg_session.execute(
        text(
            "UPDATE food_reference SET extra_nutrients = CAST(:micros AS json) WHERE id=:id"
        ),
        {"micros": '{"iron_mg":3}', "id": food_id},
    )
    await CatalogProjectionRebuilder(pg_session).rebuild((ids[0],))
    micronutrients = await facets()
    assert micronutrients[:3] == original[:3]
    assert micronutrients[3] != original[3]
    await pg_session.execute(
        text("UPDATE meal_catalog SET name='New recipe title' WHERE id=:id"),
        {"id": ids[0]},
    )
    await CatalogProjectionRebuilder(pg_session).rebuild((ids[0],))
    title = await facets()
    assert title[0] != micronutrients[0]
    assert title[1] == micronutrients[1]
    assert title[2] != micronutrients[2] and title[3] != micronutrients[3]
    pg_session.add(
        MealCatalogStepORM(
            catalog_meal_id=ids[0],
            step_number=1,
            title="Cook",
            description="Cook the rice",
        )
    )
    await pg_session.flush()
    await CatalogProjectionRebuilder(pg_session).rebuild((ids[0],))
    steps = await facets()
    assert (steps[0], steps[1], steps[3]) == (title[0], title[1], title[3])
    assert steps[2] != title[2]
