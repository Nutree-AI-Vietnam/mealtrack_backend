"""Rollback-only PostgreSQL plans over synthetic, canonically rebuilt recipes."""

import asyncio
import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import src.infra.database.models  # noqa: E402, F401
from src.infra.database.models.food_reference_model import (
    FoodReferenceModel,  # noqa: E402
)
from src.infra.database.models.meal_recommendation import (  # noqa: E402
    MealCatalogIngredientORM,
    MealCatalogORM,
)
from src.infra.repositories.catalog_projection_filters import (  # noqa: E402
    recipe_filters,
    recipe_order,
)
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,  # noqa: E402
)
from src.infra.repositories.catalog_projection_repository import (
    CatalogProjectionRepository,  # noqa: E402
)


async def run(count=1000):
    raw = os.environ.get("CATALOG_PROJECTION_DATABASE_URL", "")
    url = make_url(raw)
    if url.host not in {"127.0.0.1", "localhost"} or not (url.database or "").endswith(
        "_test"
    ):
        raise SystemExit("Benchmark requires a localhost database with _test suffix")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            food = FoodReferenceModel(
                name="Synthetic Rice",
                source="catalog_seed",
                is_verified=True,
                protein_100g=3,
                carbs_100g=28,
                fat_100g=1,
                fiber_100g=2,
                sugar_100g=0,
                density=1,
            )
            session.add(food)
            await session.flush()
            ids = []
            for index in range(count):
                identity = f"benchmark-{index:06}"
                ids.append(identity)
                session.add(
                    MealCatalogORM(
                        id=identity,
                        catalog_key=identity,
                        content_hash=hashlib.sha256(identity.encode()).hexdigest(),
                        name=f"{'Rare' if index % 100 == 0 else 'Rice'} bowl {index}",
                        cuisine="german" if index % 5 == 0 else "vietnamese",
                        description="Tofu and rice",
                        recipe_payload={},
                        payload_digest="a" * 64,
                        publication_status="published",
                        nutrition_status="ready",
                        lunch_eligible=True,
                        dinner_eligible=True,
                        is_active=index % 10 != 9,
                        popularity_rank=None if index % 7 == 0 else index % 100,
                        prep_time_minutes=index % 20,
                        cook_time_minutes=10,
                        ingredients=[
                            MealCatalogIngredientORM(
                                position=1,
                                food_reference_id=food.id,
                                display_name="Rice",
                                quantity=Decimal(100),
                                unit="g",
                                category="pantry",
                            )
                        ],
                    )
                )
            await session.flush()
            for offset in range(0, count, 500):
                await CatalogProjectionRebuilder(session).rebuild(
                    ids[offset : offset + 500]
                )
            await session.execute(text("ANALYZE meal_catalog"))
            await session.execute(text("ANALYZE meal_catalog_projection"))
            results = []
            for label, filters, offset in [
                ("all", {}, 0),
                ("cuisine", {"cuisine": "german"}, 0),
                ("substring", {"query": "rare"}, 0),
                ("cooking_time", {"max_cook_time": 20}, 0),
                ("deep_page", {}, 800),
            ]:
                base = CatalogProjectionRepository._base().where(
                    *recipe_filters(**filters)
                )
                for kind, statement in [
                    ("count", select(func.count()).select_from(base.subquery())),
                    ("page", base.order_by(*recipe_order()).limit(20).offset(offset)),
                ]:
                    sql = statement.compile(
                        dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                    )
                    plan = (
                        await session.execute(
                            text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
                        )
                    ).scalar_one()
                    results.append({"case": label, "kind": kind, "plan": plan[0]})
            await session.rollback()
            print(
                json.dumps(
                    {
                        "synthetic_recipes": count,
                        "source_writes_rolled_back": True,
                        "plans": results,
                    },
                    indent=2,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
