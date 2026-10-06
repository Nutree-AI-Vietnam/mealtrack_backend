"""Explain why catalog translation jobs fail, without writing to the database.

python -m scripts.development.diagnose_catalog_translation --limit 3

Re-translates recipes whose translation job is in retry_wait and prints, per
OpenAI request, the duration, error class, and every line the safety check
rejected. Uses the same application database URL and OpenAI key as the app.
"""

import argparse
import asyncio
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


def _short(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def run(args) -> None:
    from src.bootstrap.catalog_preparation import build_preparation_computer
    from src.domain.ports.catalog_preparation_port import PreparationTask
    from src.infra.adapters.openai_translation_adapter import (
        OpenAITranslationAdapter,
    )
    from src.infra.database.models.meal_recommendation.catalog_preparation import (
        CatalogPreparationJobORM as Job,
    )
    from src.infra.repositories.catalog_recipe_repository_async import (
        MealCatalogORM,
        _catalog_meal_detail_load_options,
        _meal_to_domain,
    )
    from src.infra.workers.catalog_preparation_runtime import create_worker_engine

    events: list[str] = []
    original_request = OpenAITranslationAdapter._request_batch
    original_safe = OpenAITranslationAdapter._safe_output

    async def timed_request(self, **kwargs):
        started = time.perf_counter()
        try:
            result = await original_request(self, **kwargs)
        except BaseException as exc:
            events.append(
                f"    request items={len(kwargs['items'])} "
                f"{time.perf_counter() - started:.1f}s ERROR {type(exc).__name__}"
            )
            raise
        events.append(
            f"    request items={len(kwargs['items'])} "
            f"{time.perf_counter() - started:.1f}s incomplete={result.incomplete} "
            f"refusal={result.refusal}"
        )
        return result

    def traced_safe(self, source, translated, target="en", source_language="en"):
        ok = original_safe(self, source, translated, target, source_language)
        if not ok:
            show = (lambda text: text) if args.full else _short
            events.append(
                f"    REJECTED {show(source)!r}\n          -> {show(translated)!r}"
            )
        return ok

    OpenAITranslationAdapter._request_batch = timed_request
    OpenAITranslationAdapter._safe_output = traced_safe

    engine = create_worker_engine(capacity=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            jobs = (
                await session.execute(
                    select(Job.catalog_meal_id, Job.locale)
                    .where(Job.task == "translation", Job.status == args.status)
                    .order_by(Job.updated_at.desc())
                    .limit(args.limit)
                )
            ).all()
            meals = {}
            for meal_id, _ in jobs:
                row = (
                    await session.execute(
                        select(MealCatalogORM)
                        .where(MealCatalogORM.id == meal_id)
                        .options(*_catalog_meal_detail_load_options())
                    )
                ).scalar_one()
                meals[meal_id] = _meal_to_domain(row, include_steps=True)
        async with httpx.AsyncClient() as client:
            computer = build_preparation_computer(http_client=client)

            class Claim:
                task = PreparationTask.TRANSLATION

            class Preparation:
                pass

            for meal_id, locale in jobs:
                meal = meals[meal_id]
                claim = Claim()
                claim.locale = locale
                preparation = Preparation()
                preparation.claim, preparation.meal = claim, meal
                events.clear()
                started = time.perf_counter()
                result = await computer.compute(preparation)
                print(
                    f"\n{meal.name!r} -> {locale}: {result.outcome.name} "
                    f"{result.error_code or ''} ({time.perf_counter() - started:.1f}s)"
                )
                print("\n".join(events) or "    (no provider request)")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--status", default="retry_wait", choices=("retry_wait", "failed")
    )
    parser.add_argument(
        "--full", action="store_true", help="print rejected texts untruncated"
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
