"""Durable preparation queue and atomic version-fenced result publication."""

import math
import random
from datetime import timedelta

from sqlalchemy import select

from src.domain.constants.languages import SUPPORTED_TRANSLATION_LANGUAGES
from src.domain.model.nutrition.micros import Micros
from src.domain.ports.catalog_preparation_port import (
    PREPARATION_CONTRACT_VERSION,
    PreparationOutcome,
    PreparationTask,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM as Projection,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import (
    MealCatalogORM as Source,
)
from src.infra.repositories.catalog_preparation_claims import CatalogPreparationClaims
from src.infra.repositories.catalog_preparation_publisher import (
    CatalogPreparationPublisher,
)
from src.infra.repositories.catalog_preparation_reads import (
    CatalogPreparationReads,
    current_facet,
)
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,
)
from src.infra.repositories.catalog_publication_fence import catalog_publication_version


class AsyncCatalogPreparationRepository(
    CatalogPreparationClaims, CatalogPreparationReads, CatalogPreparationPublisher
):
    """Callers commit claim/heartbeat/result transactions separately."""

    get_translations = CatalogPreparationReads.load_translations

    async def enqueue_for_recipes(
        self,
        recipe_ids,
        *,
        locales=("vi",),
        contract_version=PREPARATION_CONTRACT_VERSION,
    ):
        ids = tuple(dict.fromkeys(recipe_ids))
        locales = tuple(dict.fromkeys(locales))
        if len(ids) > 500 or any(
            locale not in SUPPORTED_TRANSLATION_LANGUAGES for locale in locales
        ):
            raise ValueError("Invalid preparation recipe/locale batch")
        if not ids:
            return 0
        rows = (
            (
                await self.session.execute(
                    select(Projection)
                    .join(Source, Source.id == Projection.catalog_meal_id)
                    .where(
                        Projection.catalog_meal_id.in_(ids), Source.is_active.is_(True)
                    )
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        values = []
        for row in rows:
            for task, task_locales in (
                (PreparationTask.MICRONUTRIENTS, ("",)),
                (PreparationTask.TRANSLATION, locales),
            ):
                version = current_facet(row, task)
                if version is not None:
                    values.extend(
                        {
                            "task": task.value,
                            "catalog_meal_id": row.catalog_meal_id,
                            "input_facet_version": version,
                            "locale": locale,
                            "contract_version": contract_version,
                        }
                        for locale in task_locales
                    )
        return await self.enqueue(values)

    async def backfill_page(self, *, after_id=None, limit=100, locales=("vi",)):
        if not 1 <= limit <= 500:
            raise ValueError("Invalid preparation backfill page size")
        await catalog_publication_version(self.session, shared=False)
        statement = (
            select(Source.id)
            .where(Source.is_active.is_(True))
            .order_by(Source.id)
            .limit(limit)
        )
        if after_id is not None:
            statement = statement.where(Source.id > after_id)
        ids = tuple((await self.session.execute(statement)).scalars().all())
        await CatalogProjectionRebuilder(self.session).rebuild(ids)
        count = await self.enqueue_for_recipes(ids, locales=locales)
        return count, ids[-1] if ids else None

    async def reconcile_projection(self, claim, *, locales=("vi",)):
        await catalog_publication_version(self.session, shared=False)
        job = await self.locked_claim(claim)
        if job is None:
            return False
        await CatalogProjectionRebuilder(self.session).rebuild((claim.catalog_meal_id,))
        await self.enqueue_for_recipes((claim.catalog_meal_id,), locales=locales)
        self._terminal(job, "succeeded")
        await self.session.flush()
        return True

    async def complete(self, claim, result):
        # Publication lock precedes job/source locks. Intentional USDA updates
        # rebuild the facet and publish the overlay under its resulting version.
        await catalog_publication_version(self.session, shared=False)
        job = await self.locked_claim(claim)
        if job is None:
            return False
        projection = await self.projection(claim.catalog_meal_id)
        if current_facet(projection, claim.task) != claim.input_facet_version:
            self._terminal(job, "superseded", "input_changed")
        elif result.outcome == PreparationOutcome.READY:
            if not self._valid_payload(claim.task, result.payload):
                self._terminal(job, "failed", "invalid_result")
            else:
                for item in result.reference_updates:
                    from src.infra.repositories.food_reference_repository_async import (
                        AsyncFoodReferenceRepository,
                    )

                    await AsyncFoodReferenceRepository(
                        self.session
                    ).update_usda_micronutrients(
                        item.food_reference_id, item.fdc_id, item.extra_nutrients
                    )
                if result.reference_updates:
                    await CatalogProjectionRebuilder(self.session).rebuild(
                        (claim.catalog_meal_id,)
                    )
                    projection = await self.projection(claim.catalog_meal_id)
                await self._publish(
                    claim, result.payload, current_facet(projection, claim.task)
                )
                self._terminal(job, "succeeded")
        elif result.outcome == PreparationOutcome.SUPERSEDED:
            self._terminal(job, "superseded", "input_changed")
        elif (
            result.outcome == PreparationOutcome.PERMANENT_FAILURE
            or job.attempts >= job.max_attempts
        ):
            self._terminal(job, "failed", result.error_code or "retry_exhausted")
        else:
            self._terminal(
                job, "retry_wait", result.error_code or "provider_unavailable"
            )
            delay = min(3600, 5 * 2 ** (job.attempts - 1)) * random.uniform(0.8, 1.2)
            from sqlalchemy import func

            job.available_at = (
                await self.session.execute(select(func.now()))
            ).scalar_one() + timedelta(seconds=delay)
        await self.session.flush()
        return True

    @staticmethod
    def _terminal(job, status, error=None):
        job.status, job.last_error_code = status, (error or "")[:64] or None
        job.claim_token, job.lease_expires_at = None, None

    @staticmethod
    def _valid_payload(task, payload):
        if task == PreparationTask.MICRONUTRIENTS:
            values = payload.get("micros")
            return isinstance(values, dict) and all(
                isinstance(values.get(field), (int, float))
                and not isinstance(values[field], bool)
                and math.isfinite(values[field])
                and values[field] >= 0
                for field in Micros.__dataclass_fields__
            )
        values = payload.get("translations")
        return isinstance(values, dict) and all(
            isinstance(key, str) and isinstance(value, str) and bool(value)
            for key, value in values.items()
        )
