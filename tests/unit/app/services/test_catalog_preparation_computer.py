"""Pure preparation computation outcomes and source-authority invariants."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.app.services.catalog_preparation_computer import CatalogPreparationComputer
from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
    MICRONUTRIENT_FIELDS,
    CatalogRecipeMicronutrientEnrichmentService,
)
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.ports.catalog_preparation_port import (
    PreparationClaim,
    PreparationInput,
    PreparationOutcome,
    PreparationTask,
)
from src.infra.workers.catalog_preparation_worker import CatalogPreparationWorker


def _input(task=PreparationTask.MICRONUTRIENTS, locale=""):
    meal = CatalogMeal(
        id="recipe",
        catalog_key="rice",
        content_hash="a" * 64,
        name="Rice",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(1),
        carbs_g=Decimal(2),
        fat_g=Decimal(0),
        fiber_g=Decimal(0),
    )
    claim = PreparationClaim(
        "job",
        task,
        meal.id,
        "b" * 64,
        locale,
        "v1",
        "token",
        1,
        5,
        datetime.now(UTC) + timedelta(seconds=120),
    )
    return PreparationInput(claim, meal)


@pytest.mark.asyncio
async def test_compute_has_no_hidden_uow_and_validates_every_micronutrient():
    def forbidden_uow():
        pytest.fail("Pure compute opened a transaction")

    async def estimate(_meal, missing, known):
        assert not known
        return dict.fromkeys(missing, 1)

    service = CatalogRecipeMicronutrientEnrichmentService(
        forbidden_uow, estimator=estimate
    )
    result = await service.compute(_input())
    assert result.outcome == PreparationOutcome.READY
    assert set(result.payload["micros"]) == set(MICRONUTRIENT_FIELDS)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, True, -1, float("nan"), float("inf")])
async def test_invalid_provider_field_is_explicit_retryable_failure(invalid):
    async def estimate(_meal, missing, known):
        return {**dict.fromkeys(missing, 1), missing[0]: invalid}

    result = await CatalogRecipeMicronutrientEnrichmentService(
        None, estimator=estimate
    ).compute(_input())
    assert result.outcome == PreparationOutcome.RETRYABLE_FAILURE
    assert result.error_code == "incomplete_micronutrients"
    assert not result.payload


@pytest.mark.asyncio
async def test_usda_hydration_is_staged_and_known_reference_values_win():
    from dataclasses import replace

    preparation = replace(
        _input(),
        references=(
            {
                "id": 1,
                "fdc_id": 123,
                "recipe_grams": 100,
                "extra_nutrients": {
                    "iron": {"amount": 2, "unit": "mg", "source": "usda_fdc"}
                },
            },
        ),
    )

    async def fdc(ids):
        assert ids == [123]
        return {
            123: {
                "iron": {"amount": 99, "unit": "mg"},
                "calcium": {"amount": 3, "unit": "mg"},
            }
        }

    async def estimate(_meal, missing, known):
        assert known["iron"] == 2
        assert known["calcium"] == 3
        return dict.fromkeys(missing, 1)

    result = await CatalogRecipeMicronutrientEnrichmentService(
        None, estimator=estimate, fdc_loader=fdc
    ).compute(preparation)
    assert result.outcome == PreparationOutcome.READY
    assert result.payload["micros"]["iron"] == 2
    assert result.reference_updates[0].extra_nutrients == {
        "calcium": {"amount": 3, "unit": "mg", "source": "usda_fdc"}
    }
    assert preparation.references[0]["extra_nutrients"] == {
        "iron": {"amount": 2, "unit": "mg", "source": "usda_fdc"}
    }


@pytest.mark.asyncio
async def test_missing_provider_is_permanent_and_timeout_is_retryable():
    result = await CatalogRecipeMicronutrientEnrichmentService(None).compute(_input())
    assert result.outcome == PreparationOutcome.PERMANENT_FAILURE

    async def timeout(*_):
        raise TimeoutError("Private provider diagnostic must not become job data")

    result = await CatalogRecipeMicronutrientEnrichmentService(
        None, estimator=timeout
    ).compute(_input())
    assert result.outcome == PreparationOutcome.RETRYABLE_FAILURE
    assert result.error_code == "provider_unavailable"


@pytest.mark.asyncio
async def test_translation_partial_result_is_not_published():
    class Translator:
        async def translate_texts(self, texts, source, locale):
            return TranslationResult(
                tuple(texts), TranslationOutcome.PARTIAL, source, locale
            )

    computer = CatalogPreparationComputer(
        micronutrient_computer=None, translation_service=Translator()
    )
    result = await computer.compute(_input(PreparationTask.TRANSLATION, "vi"))
    assert result.outcome == PreparationOutcome.RETRYABLE_FAILURE
    assert not result.payload


@pytest.mark.asyncio
async def test_worker_provider_deadline_returns_typed_failure():
    import asyncio

    class Computer:
        async def compute(self, _):
            await asyncio.Event().wait()

    worker = CatalogPreparationWorker(None, Computer(), provider_deadline_seconds=0.01)
    result = await worker._compute(_input())
    assert result.outcome == PreparationOutcome.RETRYABLE_FAILURE
    assert result.error_code == "provider_deadline"
