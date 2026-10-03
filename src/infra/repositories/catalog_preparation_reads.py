"""Current source snapshots and prepared overlays, with no provider side effects."""

from sqlalchemy import select

from src.domain.constants.languages import SUPPORTED_TRANSLATION_LANGUAGES
from src.domain.ports.catalog_preparation_port import (
    PREPARATION_CONTRACT_VERSION,
    PreparationInput,
    PreparationTask,
)
from src.infra.database.models.meal_recommendation.catalog_micronutrient_enrichment import (
    MealCatalogMicronutrientEnrichmentORM as Micro,
)
from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogRecipeTranslationORM as Translation,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM as Projection,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import (
    MealCatalogORM as Source,
)
from src.infra.repositories.catalog_publication_fence import catalog_publication_version


def current_facet(projection, task):
    if projection is None or projection.schema_version != 1:
        return None
    if task == PreparationTask.TRANSLATION:
        return None if projection.translation_dirty else projection.translation_digest
    if projection.enrichment_dirty or projection.nutrition_dirty:
        return None
    return projection.enrichment_digest


class CatalogPreparationReads:
    def __init__(self, session):
        self.session = session

    async def projection(self, recipe_id):
        return (
            await self.session.execute(
                select(Projection)
                .join(Source, Source.id == Projection.catalog_meal_id)
                .where(
                    Projection.catalog_meal_id == recipe_id, Source.is_active.is_(True)
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def load_translations(
        self, recipe_ids, *, locale, contract_version=PREPARATION_CONTRACT_VERSION
    ):
        ids = tuple(dict.fromkeys(recipe_ids))
        if len(ids) > 500:
            raise ValueError("Translation read batch exceeds 500 recipes")
        if not ids or locale not in SUPPORTED_TRANSLATION_LANGUAGES:
            return {}
        rows = (
            (
                await self.session.execute(
                    select(Translation)
                    .join(
                        Projection,
                        Projection.catalog_meal_id == Translation.catalog_meal_id,
                    )
                    .join(Source, Source.id == Translation.catalog_meal_id)
                    .where(
                        Translation.catalog_meal_id.in_(ids),
                        Translation.locale == locale,
                        Translation.contract_version == contract_version,
                        Translation.input_facet_version
                        == Projection.translation_digest,
                        Projection.translation_dirty.is_(False),
                        Projection.schema_version == 1,
                        Source.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.catalog_meal_id: dict(row.translations) for row in rows}

    async def get_overlay(
        self, recipe_id, *, contract_version=PREPARATION_CONTRACT_VERSION
    ):
        row = (
            await self.session.execute(
                select(Micro)
                .join(Projection, Projection.catalog_meal_id == Micro.catalog_meal_id)
                .join(Source, Source.id == Micro.catalog_meal_id)
                .where(
                    Micro.catalog_meal_id == recipe_id,
                    Micro.status == "ready",
                    Micro.input_facet_version == Projection.enrichment_digest,
                    Micro.preparation_contract_version == contract_version,
                    Projection.enrichment_dirty.is_(False),
                    Projection.nutrition_dirty.is_(False),
                    Projection.schema_version == 1,
                    Source.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        return (
            {"micros": dict(row.micros), "sources": dict(row.sources)} if row else None
        )

    async def load_input(self, claim):
        from src.infra.repositories.catalog_recipe_repository_async import (
            _catalog_meal_detail_load_options,
            _meal_to_domain,
            _resolve_ingredient_nutrition,
        )
        from src.infra.repositories.food_reference_projection import (
            food_reference_model_to_dict,
            food_reference_model_to_nutrition_projection,
        )

        await catalog_publication_version(self.session, shared=True)
        # The publication fence may have waited beyond this claim's lease.
        # Validate the token under a short job lock before any provider input
        # escapes this transaction, and again after the canonical snapshot load.
        if await self.locked_claim(claim) is None:
            return None
        projection = await self.projection(claim.catalog_meal_id)
        if current_facet(projection, claim.task) != claim.input_facet_version:
            return None
        source = (
            await self.session.execute(
                select(Source)
                .where(Source.id == claim.catalog_meal_id, Source.is_active.is_(True))
                .options(*_catalog_meal_detail_load_options())
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if source is None:
            return None
        references = {}
        for line in source.ingredients:
            if line.food_reference is None:
                continue
            reference = line.food_reference
            if reference.id not in references:
                values = food_reference_model_to_dict(reference)
                values["extra_nutrients"] = (
                    food_reference_model_to_nutrition_projection(
                        reference, preserve_nutrient_units=True
                    ).extra_nutrients
                )
                references[reference.id] = {**values, "recipe_grams": 0}
            references[reference.id]["recipe_grams"] += _resolve_ingredient_nutrition(
                line
            ).grams
        cached = await self.get_overlay(claim.catalog_meal_id)
        if await self.locked_claim(claim) is None:
            return None
        return PreparationInput(
            claim,
            _meal_to_domain(source, include_steps=True),
            tuple(references.values()),
            cached,
        )
