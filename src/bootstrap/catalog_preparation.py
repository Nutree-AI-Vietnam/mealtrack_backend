"""Provider composition for the dedicated preparation process."""

from src.app.services.catalog_preparation_computer import CatalogPreparationComputer
from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
    CatalogRecipeMicronutrientEnrichmentService,
    build_micronutrient_estimate_prompt,
)
from src.domain.model.ai.nutrition_contracts import AIRecipeMicronutrientEstimate
from src.domain.services.food_mapping_service import FoodMappingService
from src.domain.services.translation.text_translation_service import (
    TextTranslationService,
)
from src.infra.adapters.food_data_service import FoodDataService
from src.infra.adapters.openai_translation_adapter import OpenAITranslationAdapter
from src.infra.config.settings import settings
from src.infra.services.ai.providers.openai_provider import OpenAIProvider


def build_preparation_computer(*, http_client):
    """Independent worker provider instance, no shared API admission or retries."""
    estimator, translator = None, None
    if settings.OPENAI_API_KEY:
        provider = OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            request_timeout_seconds=25,
            max_retries=0,
            store_responses=False,
            prompt_cache_enabled=settings.OPENAI_PROMPT_CACHE_ENABLED,
            prompt_cache_retention=settings.OPENAI_PROMPT_CACHE_RETENTION,
            prompt_cache_key_prefix=settings.OPENAI_PROMPT_CACHE_KEY_PREFIX,
        )

        async def estimate(meal, missing, known):
            return await provider.generate(
                model=settings.OPENAI_TEXT_MODEL,
                prompt=build_micronutrient_estimate_prompt(meal, missing, known),
                system_message="Estimate missing whole-recipe nutrients using USDA profiles. Preserve supplied authoritative values, use the schema units, and never supply calories or macros.",
                response_type="json",
                max_tokens=700,
                schema=AIRecipeMicronutrientEstimate,
                purpose_hint="catalog_preparation",
            )

        estimator = estimate
        translator = TextTranslationService(
            OpenAITranslationAdapter(
                provider=provider,
                model=settings.OPENAI_TRANSLATION_MODEL,
                timeout_seconds=25,
            )
        )
    fdc_loader = None
    if settings.USDA_FDC_API_KEY:
        service = FoodDataService(api_key=settings.USDA_FDC_API_KEY, client=http_client)
        mapper = FoodMappingService()

        async def load_fdc(ids):
            nutrients = {}
            # Sequential inside one provider lane: USDA fanout cannot multiply
            # the process-wide preparation admission limit.
            for identifier in ids[:12]:
                mapped = mapper.map_food_details(
                    await service.get_food_details(identifier)
                )
                if mapped.get("fdc_id") != identifier:
                    raise ValueError(
                        "USDA record does not match its requested identity"
                    )
                if mapped.get("extra_nutrients"):
                    nutrients[identifier] = mapped["extra_nutrients"]
            return nutrients

        fdc_loader = load_fdc
    micronutrients = CatalogRecipeMicronutrientEnrichmentService(
        None, estimator=estimator, fdc_loader=fdc_loader
    )
    return CatalogPreparationComputer(
        micronutrient_computer=micronutrients, translation_service=translator
    )
