"""Standard catalog publishers refresh query and nutrition data before commit."""

import hashlib

import pytest
from sqlalchemy import select
from tests.integration.postgres.catalog_projection_fixtures import seed_catalog

from src.domain.ports.catalog_recipe_repository_port import (
    CatalogMealSeedIngredientWrite,
    CatalogMealSeedWrite,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import MealCatalogORM
from src.infra.repositories.admin_meal_catalog_repository_async import (
    AsyncAdminMealCatalogRepository,
)
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_seed_replacement_rank_and_admin_image_publish_clean_projection(
    pg_session, monkeypatch
):
    ids, food_id = await seed_catalog(pg_session)
    repo = AsyncCatalogMealRepository(pg_session, projections_enabled=True)
    source = (
        await pg_session.execute(
            select(MealCatalogORM).where(MealCatalogORM.id == ids[0])
        )
    ).scalar_one()
    await repo.add_seed_meal(
        CatalogMealSeedWrite(
            catalog_key=source.catalog_key,
            content_hash=hashlib.sha256(b"replacement-title").hexdigest(),
            name="Published replacement bowl",
            cuisine="Vietnamese",
            description="New description",
            image_url=None,
            meal_types=("lunch", "dinner"),
            popularity_rank=5,
            ingredients=(
                CatalogMealSeedIngredientWrite(
                    display_name="Rice",
                    quantity=200,
                    unit="g",
                    food_reference_id=food_id,
                ),
            ),
        )
    )

    async def projection():
        return (
            await pg_session.execute(
                select(MealCatalogProjectionORM)
                .where(MealCatalogProjectionORM.catalog_meal_id == ids[0])
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    row = await projection()
    assert not row.query_dirty and not row.nutrition_dirty
    assert row.name_casefold == "published replacement bowl"
    assert row.protein_g == 6
    before_update = row.updated_at
    await repo.update_popularity_rank(catalog_key=source.catalog_key, popularity_rank=1)
    row = await projection()
    assert row.popularity_rank == 1 and not row.query_dirty and not row.nutrition_dirty
    assert row.updated_at >= before_update
    monkeypatch.setenv("CATALOG_PROJECTIONS_ENABLED", "true")
    admin = AsyncAdminMealCatalogRepository(pg_session)
    assert await admin.set_image_url(ids[0], "https://example.test/image.png")
    row = await projection()
    assert not row.query_dirty and not row.nutrition_dirty
    assert row.summary_payload["image_url"] == "https://example.test/image.png"
    # No intermediate commit was needed to publish a current projection.
    summary = (await repo.get_meal_summaries((ids[0],)))[0]
    assert summary.name == "Published replacement bowl" and summary.protein_g == 6
