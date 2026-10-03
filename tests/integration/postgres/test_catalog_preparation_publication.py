"""Output/job atomicity and dependency-version publication on PG."""

import pytest
from sqlalchemy import delete, select, update
from tests.integration.postgres.catalog_preparation_fixtures import (
    claim,
    job_state,
    micros_result,
    seed_preparation,
)

from src.domain.ports.catalog_preparation_port import (
    PreparationOutcome,
    PreparationResult,
    ReferenceNutrientUpdate,
)
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.meal_recommendation.catalog_micronutrient_enrichment import (
    MealCatalogMicronutrientEnrichmentORM as Micro,
)
from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogPreparationJobORM as Job,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM as Projection,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import (
    MealCatalogORM as Source,
)
from src.infra.repositories.catalog_preparation_repository import (
    AsyncCatalogPreparationRepository,
)
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_translation_output_and_job_commit_atomically_with_rollback_recovery(
    pg_session, async_session_factory
):
    ids, _ = await seed_preparation(pg_session, task="translation")
    claimed = await claim(async_session_factory)
    ready = PreparationResult(
        PreparationOutcome.READY, {"translations": {"Rice": "Cơm"}}
    )
    async with async_session_factory() as writer:
        assert await AsyncCatalogPreparationRepository(writer).complete(claimed, ready)
        async with async_session_factory() as reader:
            assert (
                await AsyncCatalogPreparationRepository(reader).load_translations(
                    ids[:1], locale="vi"
                )
                == {}
            )
            assert (await job_state(reader, claimed.id)).status == "running"
        await writer.rollback()
    async with async_session_factory() as writer:
        assert await AsyncCatalogPreparationRepository(writer).complete(claimed, ready)
        await writer.commit()
        assert not await AsyncCatalogPreparationRepository(writer).complete(
            claimed, ready
        )
    async with async_session_factory() as reader:
        assert await AsyncCatalogPreparationRepository(reader).load_translations(
            ids[:1], locale="vi"
        ) == {ids[0]: {"Rice": "Cơm"}}
        assert (await job_state(reader, claimed.id)).status == "succeeded"


@pytest.mark.asyncio
async def test_dirty_dependency_supersedes_old_claim_and_durable_producer_survives(
    pg_session, async_session_factory
):
    ids, _ = await seed_preparation(pg_session)
    claimed = await claim(async_session_factory)
    async with async_session_factory() as writer:
        await writer.execute(
            update(Source)
            .where(Source.id == ids[0])
            .values(name="Changed source prompt")
        )
        await writer.commit()
    async with async_session_factory() as session:
        repo = AsyncCatalogPreparationRepository(session)
        assert await repo.load_input(claimed) is None
        assert await repo.complete(claimed, micros_result())
        await session.commit()
        assert (await job_state(session, claimed.id)).status == "superseded"
        assert await repo.get_overlay(ids[0]) is None
        assert (
            (
                await session.execute(
                    select(Job).where(
                        Job.catalog_meal_id == ids[0], Job.task == "projection"
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_cosmetic_hash_change_keeps_versioned_overlay_without_unique_collision(
    pg_session, async_session_factory
):
    ids, _ = await seed_preparation(pg_session)
    first = await claim(async_session_factory)
    async with async_session_factory() as session:
        await AsyncCatalogPreparationRepository(session).complete(
            first, micros_result(2)
        )
        await session.commit()
    async with async_session_factory() as session:
        legacy_hash = (
            await session.execute(
                select(Micro.content_hash).where(Micro.catalog_meal_id == ids[0])
            )
        ).scalar_one()
        await session.execute(
            update(Source)
            .where(Source.id == ids[0])
            .values(content_hash="c" * 64, image_url="https://example.test/image.png")
        )
        await CatalogProjectionRebuilder(session).rebuild(ids[:1])
        await session.execute(delete(Job).where(Job.task != "micronutrients"))
        await session.execute(
            update(Job).where(Job.id == first.id).values(status="pending", attempts=0)
        )
        await session.commit()
        assert (await AsyncCatalogPreparationRepository(session).get_overlay(ids[0]))[
            "micros"
        ]["iron"] == 2
    second = await claim(async_session_factory)
    async with async_session_factory() as session:
        assert await AsyncCatalogPreparationRepository(session).complete(
            second, micros_result(4)
        )
        await session.commit()
        assert (await AsyncCatalogPreparationRepository(session).get_overlay(ids[0]))[
            "micros"
        ]["iron"] == 4
        assert (
            await session.execute(
                select(Micro.content_hash).where(Micro.catalog_meal_id == ids[0])
            )
        ).scalar_one() == legacy_hash


@pytest.mark.asyncio
async def test_intentional_usda_update_publishes_matching_resulting_facet(
    pg_session, async_session_factory
):
    ids, food_id = await seed_preparation(pg_session)
    await pg_session.execute(
        update(FoodReferenceModel)
        .where(FoodReferenceModel.id == food_id)
        .values(fdc_id=123)
    )
    await CatalogProjectionRebuilder(pg_session).rebuild(ids)
    await pg_session.execute(delete(Job))
    await AsyncCatalogPreparationRepository(pg_session).enqueue_for_recipes(
        ids[:1], locales=()
    )
    await pg_session.commit()
    claimed = await claim(async_session_factory)
    from dataclasses import replace

    ready = replace(
        micros_result(),
        reference_updates=(
            ReferenceNutrientUpdate(
                food_id, 123, {"calcium": {"amount": 3, "unit": "mg"}}
            ),
        ),
    )
    async with async_session_factory() as session:
        assert await AsyncCatalogPreparationRepository(session).complete(claimed, ready)
        await session.commit()
    async with async_session_factory() as session:
        version = (
            await session.execute(
                select(Projection.enrichment_digest).where(
                    Projection.catalog_meal_id == ids[0]
                )
            )
        ).scalar_one()
        overlay = (
            await session.execute(select(Micro).where(Micro.catalog_meal_id == ids[0]))
        ).scalar_one()
        assert version != claimed.input_facet_version
        assert overlay.input_facet_version == version
        assert (
            await AsyncCatalogPreparationRepository(session).get_overlay(ids[0])
            is not None
        )
        assert (await job_state(session, claimed.id)).status == "succeeded"
