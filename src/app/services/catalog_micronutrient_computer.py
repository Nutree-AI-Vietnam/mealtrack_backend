"""Source-first nutrient computation; caller owns snapshots and final publication."""

from dataclasses import replace

from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
    MICRONUTRIENT_FIELDS,
    _cached_values,
    _has_all_micro_fields,
    _micros_from_reference,
    _validated_estimate,
)
from src.domain.model.nutrition.extra_nutrients import (
    complete_micros_from_per_100g_portions,
)
from src.domain.ports.catalog_preparation_port import (
    PreparationInput,
    PreparationOutcome,
    PreparationResult,
    ReferenceNutrientUpdate,
)


async def compute_micronutrients(
    preparation: PreparationInput, *, estimator, fdc_loader=None
) -> PreparationResult:
    """Return explicit ready/failure outcomes; no inner UoW or persistence."""
    references = [dict(row) for row in preparation.references]
    ids = sorted(
        {
            int(row["fdc_id"])
            for row in references
            if row.get("fdc_id") is not None
            and len(_micros_from_reference(row)) < len(MICRONUTRIENT_FIELDS)
        }
    )[:12]
    updates = []
    try:
        fetched = await fdc_loader(ids) if fdc_loader is not None and ids else {}
        for row in references:
            values = fetched.get(row.get("fdc_id"))
            if not values:
                continue
            known = _micros_from_reference(row)
            extra = dict(row.get("extra_nutrients") or {})
            additions = {
                key: {**value, "source": "usda_fdc"}
                if isinstance(value, dict)
                else value
                for key, value in values.items()
                if key not in known
            }
            if additions:
                row["extra_nutrients"] = {**extra, **additions}
                updates.append(
                    ReferenceNutrientUpdate(
                        int(row["id"]), int(row["fdc_id"]), additions
                    )
                )
        hydrated = complete_micros_from_per_100g_portions(
            (row.get("extra_nutrients"), float(row.get("recipe_grams", 0)))
            for row in references
        )
        meal = (
            replace(preparation.meal, nutrition_micros=hydrated)
            if updates
            else preparation.meal
        )
        source = meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
        cached = _cached_values(preparation.cached_micros)
        known = {**cached, **source}
        missing = tuple(field for field in MICRONUTRIENT_FIELDS if field not in known)
        if missing and estimator is None:
            return PreparationResult(
                PreparationOutcome.PERMANENT_FAILURE, error_code="provider_unconfigured"
            )
        estimated = (
            _validated_estimate(await estimator(meal, missing, known), missing)
            if missing
            else {}
        )
        values = {**cached, **estimated, **source}
        if not _has_all_micro_fields(values):
            return PreparationResult(
                PreparationOutcome.RETRYABLE_FAILURE,
                error_code="incomplete_micronutrients",
            )
        sources = dict((preparation.cached_micros or {}).get("sources") or {})
        sources.update(dict.fromkeys(estimated, "ai_estimate"))
        for field in source:
            all_usda = bool(references) and all(
                field
                in _micros_from_reference(
                    {
                        "extra_nutrients": {
                            key: value
                            for key, value in (row.get("extra_nutrients") or {}).items()
                            if isinstance(value, dict)
                            and value.get("source") == "usda_fdc"
                        }
                    }
                )
                for row in references
            )
            sources[field] = "usda_fdc" if all_usda else "food_reference"
        return PreparationResult(
            PreparationOutcome.READY,
            {"micros": values, "sources": sources},
            reference_updates=tuple(updates),
        )
    except (ValueError, TypeError, KeyError):
        return PreparationResult(
            PreparationOutcome.PERMANENT_FAILURE, error_code="invalid_preparation_input"
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        permanent = (
            isinstance(status, int)
            and 400 <= status < 500
            and status not in (408, 409, 429)
        )
        return PreparationResult(
            PreparationOutcome.PERMANENT_FAILURE
            if permanent
            else PreparationOutcome.RETRYABLE_FAILURE,
            error_code="provider_rejected" if permanent else "provider_unavailable",
        )
