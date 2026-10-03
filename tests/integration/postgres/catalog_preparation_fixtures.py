"""Small source-backed fixtures for preparation recovery tests."""

from sqlalchemy import delete, select
from tests.integration.postgres.catalog_projection_fixtures import seed_catalog

from src.domain.model.nutrition.micros import Micros
from src.domain.ports.catalog_preparation_port import (
    PreparationOutcome,
    PreparationResult,
)
from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogPreparationJobORM as Job,
)
from src.infra.repositories.catalog_preparation_repository import (
    AsyncCatalogPreparationRepository,
)


async def seed_preparation(session, *, task="micronutrients", all_recipes=False):
    ids, food_id = await seed_catalog(session)
    await session.execute(delete(Job))
    await AsyncCatalogPreparationRepository(session).enqueue_for_recipes(
        ids if all_recipes else ids[:1], locales=("vi",)
    )
    if task is not None:
        await session.execute(delete(Job).where(Job.task != task))
    await session.commit()
    return ids, food_id


async def claim(factory, *, capacity=4):
    async with factory() as session:
        claimed = await AsyncCatalogPreparationRepository(session).claim_next(
            global_capacity=capacity
        )
        await session.commit()
        return claimed


def micros_result(value=1):
    return PreparationResult(
        PreparationOutcome.READY,
        {
            "micros": dict.fromkeys(Micros.__dataclass_fields__, value),
            "sources": dict.fromkeys(Micros.__dataclass_fields__, "ai_estimate"),
        },
    )


async def job_state(session, job_id):
    session.expire_all()
    return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
