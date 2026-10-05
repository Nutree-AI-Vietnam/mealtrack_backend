"""Validated prepared output writes in the caller's fenced transaction."""

from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.domain.ports.catalog_preparation_port import PreparationTask
from src.infra.database.models.meal_recommendation.catalog_micronutrient_enrichment import (
    MealCatalogMicronutrientEnrichmentORM as Micro,
)
from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogRecipeTranslationORM as Translation,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import (
    MealCatalogORM as Source,
)


class CatalogPreparationPublisher:
    async def _publish(self, claim, payload, version):
        insert = (
            sqlite_insert
            if self.session.get_bind().dialect.name == "sqlite"
            else pg_insert
        )
        if claim.task == PreparationTask.TRANSLATION:
            statement = insert(Translation).values(
                id=str(uuid4()),
                catalog_meal_id=claim.catalog_meal_id,
                input_facet_version=version,
                locale=claim.locale,
                contract_version=claim.contract_version,
                translations=payload["translations"],
            )
            statement = statement.on_conflict_do_update(
                index_elements=[
                    "catalog_meal_id",
                    "input_facet_version",
                    "locale",
                    "contract_version",
                ],
                set_={
                    "translations": statement.excluded.translations,
                    "updated_at": func.clock_timestamp(),
                },
            )
        else:
            content_hash = (
                await self.session.execute(
                    select(Source.content_hash).where(
                        Source.id == claim.catalog_meal_id
                    )
                )
            ).scalar_one()
            # Facet identity is authoritative for prepared results. Retain the
            # legacy hash so cosmetic edits do not erase cache-compatible rows.
            existing = (
                await self.session.execute(
                    select(Micro.id).where(
                        Micro.catalog_meal_id == claim.catalog_meal_id,
                        Micro.input_facet_version == version,
                        Micro.preparation_contract_version == claim.contract_version,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                await self.session.execute(
                    update(Micro)
                    .where(Micro.id == existing)
                    .values(
                        micros=payload["micros"],
                        sources=payload.get("sources", {}),
                        status="ready",
                        claim_token=None,
                        lease_expires_at=None,
                        retry_after=None,
                        updated_at=func.clock_timestamp(),
                    )
                )
                return
            values = {
                "catalog_meal_id": claim.catalog_meal_id,
                "content_hash": content_hash,
                "micros": payload["micros"],
                "sources": payload.get("sources", {}),
                "status": "ready",
                "claim_token": None,
                "lease_expires_at": None,
                "retry_after": None,
                "input_facet_version": version,
                "preparation_contract_version": claim.contract_version,
                "updated_at": func.clock_timestamp(),
            }
            statement = insert(Micro).values(id=str(uuid4()), **values)
            statement = statement.on_conflict_do_update(
                index_elements=["catalog_meal_id", "content_hash"], set_=values
            )
        await self.session.execute(statement)
