"""Provider-neutral durable catalog preparation contracts."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from src.domain.model.meal_recommendation import CatalogMeal

PREPARATION_CONTRACT_VERSION = "v1"
NON_TRANSLATION_LOCALE = ""


class PreparationTask(StrEnum):
    PROJECTION = "projection"
    MICRONUTRIENTS = "micronutrients"
    TRANSLATION = "translation"


class PreparationOutcome(StrEnum):
    READY = "ready"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class PreparationClaim:
    id: str
    task: PreparationTask
    catalog_meal_id: str
    input_facet_version: str
    locale: str
    contract_version: str
    claim_token: str
    attempts: int
    max_attempts: int
    lease_expires_at: datetime
    created_at: datetime | None = None


@dataclass(frozen=True)
class ReferenceNutrientUpdate:
    food_reference_id: int
    fdc_id: int
    extra_nutrients: dict[str, Any]


@dataclass(frozen=True)
class PreparationResult:
    outcome: PreparationOutcome
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    reference_updates: tuple[ReferenceNutrientUpdate, ...] = ()


@dataclass(frozen=True)
class PreparationInput:
    claim: PreparationClaim
    meal: CatalogMeal
    references: tuple[dict[str, Any], ...] = ()
    cached_micros: dict[str, Any] | None = None


class CatalogPreparationComputer(Protocol):
    async def compute(self, preparation: PreparationInput) -> PreparationResult: ...


class CatalogTranslationReader(Protocol):
    async def get_translations(
        self,
        recipe_ids: tuple[str, ...],
        *,
        locale: str,
        contract_version: str = PREPARATION_CONTRACT_VERSION,
    ) -> dict[str, dict[str, str]]:
        """Read current, clean prepared presentation fields; never enqueue."""
        ...
