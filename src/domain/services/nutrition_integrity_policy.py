"""Pure, versioned validation for structured per-100g nutrition."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.domain.model.nutrition import MAX_FOOD_ITEM_QUANTITY, Macros

NUTRITION_INTEGRITY_POLICY_VERSION = "nutrition_integrity_v1"
ACCEPTED_REASON = "accepted"
DEFAULT_GRAM_SERVING = {"unit": "g", "gram_weight": 1.0, "description": "1 g"}
_GRAM_UNITS = {"g", "gram", "grams", "gramme", "grammes"}
_PROVIDER_SOURCES = {"fatsecret", "openfoodfacts", "provider"}

# The boundary cases are kept beside the policy so other enforcement layers can
# consume the exact V1 contract without re-encoding numeric thresholds.
NUTRITION_INTEGRITY_V1_FIXTURE_MATRIX = (
    ("missing macro", {"fat_100g": None}, False, "missing_macro"),
    ("missing energy", {"calories_100g": None}, False, "energy_required"),
    ("nan macro", {"protein_100g": math.nan}, False, "invalid_macro"),
    ("infinite energy", {"calories_100g": math.inf}, False, "invalid_energy"),
    ("macro over bound", {"protein_100g": 100.0001}, False, "macro_out_of_range"),
    (
        "macro mass over bound",
        {"protein_100g": 40, "carbs_100g": 40, "fat_100g": 30.0001},
        False,
        "macro_mass_out_of_range",
    ),
    (
        "fiber over bound",
        {"fiber_100g": 100.0001},
        False,
        "fiber_or_sugar_out_of_range",
    ),
    (
        "energy over bound",
        {
            "protein_100g": 10,
            "carbs_100g": 0,
            "fat_100g": 100,
            "fiber_100g": 0,
            "calories_100g": 900,
        },
        False,
        "energy_out_of_range",
    ),
    ("energy mismatch", {"calories_100g": 200}, False, "energy_mismatch"),
    (
        "energy tolerance inclusive",
        {
            "protein_100g": 1,
            "carbs_100g": 0,
            "fat_100g": 0,
            "fiber_100g": 0,
            "sugar_100g": 0,
            "calories_100g": 24,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "zero inclusive",
        {
            "protein_100g": 0,
            "carbs_100g": 0,
            "fat_100g": 0,
            "fiber_100g": 0,
            "sugar_100g": 0,
            "calories_100g": 0,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "protein 100 inclusive",
        {
            "protein_100g": 100,
            "carbs_100g": 0,
            "fat_100g": 0,
            "fiber_100g": 0,
            "sugar_100g": 0,
            "calories_100g": 400,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "macro mass 110 inclusive",
        {
            "protein_100g": 40,
            "carbs_100g": 40,
            "fat_100g": 30,
            "fiber_100g": 0,
            "sugar_100g": 0,
            "calories_100g": 590,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "energy 900 inclusive",
        {
            "protein_100g": 0,
            "carbs_100g": 0,
            "fat_100g": 100,
            "fiber_100g": 0,
            "sugar_100g": 0,
            "calories_100g": 900,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "fiber greater than carbs",
        {
            "protein_100g": 0,
            "carbs_100g": 1,
            "fat_100g": 0,
            "fiber_100g": 2,
            "sugar_100g": 0,
            "calories_100g": 8,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "fiber plus sugar greater than carbs",
        {
            "protein_100g": 0,
            "carbs_100g": 1,
            "fat_100g": 0,
            "fiber_100g": 0,
            "sugar_100g": 2,
            "calories_100g": 4,
        },
        True,
        ACCEPTED_REASON,
    ),
    (
        "bad canonical gram",
        {"allowed_units": [{"unit": "g", "gram_weight": 100}]},
        False,
        "invalid_base_gram",
    ),
    (
        "bad serving weight",
        {"allowed_units": [{"unit": "cup", "gram_weight": 10001}]},
        False,
        "invalid_serving",
    ),
)


@dataclass(frozen=True)
class NutritionIntegrityResult:
    """Stable result shared by domain, application, and infrastructure callers."""

    accepted: bool
    reason_code: str
    policy_version: str = NUTRITION_INTEGRITY_POLICY_VERSION
    protein_100g: float | None = None
    carbs_100g: float | None = None
    fat_100g: float | None = None
    fiber_100g: float | None = None
    sugar_100g: float | None = None
    calories_100g: float | None = None
    derived_calories_100g: float | None = None
    serving_options: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    origin: str | None = None


class NutritionIntegrityError(ValueError):
    """Raised when a trust boundary receives invalid structured nutrition."""

    def __init__(self, result: NutritionIntegrityResult):
        self.result = result
        super().__init__(f"nutrition integrity rejected: {result.reason_code}")


class NutritionIntegrityPolicy:
    """Apply the frozen V1 numeric and serving invariants without I/O."""

    version = NUTRITION_INTEGRITY_POLICY_VERSION

    def evaluate(
        self,
        data: Mapping[str, Any],
        *,
        require_energy: bool = True,
        require_metric_basis: bool = False,
        provider_100g_label: bool = False,
        origin_fields: Mapping[str, Any] | None = None,
        require_macros: bool = True,
    ) -> NutritionIntegrityResult:
        normalized_origin = None
        if origin_fields is not None:
            origin_result = normalize_logical_origin(origin_fields)
            if not origin_result.accepted:
                return self._reject(origin_result.reason_code)
            normalized_origin = origin_result.origin

        values: dict[str, float] = {}
        for field_name in ("protein_100g", "carbs_100g", "fat_100g"):
            raw = data.get(field_name)
            if raw is None:
                if require_macros:
                    return self._reject("missing_macro")
                return self._display_result_without_macros(
                    data,
                    provider_100g_label=provider_100g_label,
                    origin=normalized_origin,
                )
            converted = _finite_float(raw)
            if converted is None:
                return self._reject("invalid_macro")
            if not 0.0 <= converted <= 100.0:
                return self._reject("macro_out_of_range")
            values[field_name] = converted

        fiber = _optional_finite_float(data.get("fiber_100g", data.get("fiber")))
        sugar = _optional_finite_float(data.get("sugar_100g", data.get("sugar")))
        if fiber is None or sugar is None:
            return self._reject("invalid_fiber_or_sugar")
        if not 0.0 <= fiber <= 100.0 or not 0.0 <= sugar <= 100.0:
            return self._reject("fiber_or_sugar_out_of_range")
        if sum(values.values()) > 110.0:
            return self._reject("macro_mass_out_of_range")

        derived = Macros.raw_total_calories(
            values["protein_100g"], values["carbs_100g"], values["fat_100g"], fiber
        )
        if not math.isfinite(derived) or not 0.0 <= derived <= 900.0:
            return self._reject("energy_out_of_range")

        advertised = data.get("calories_100g", data.get("calories"))
        energy = None if advertised is None else _finite_float(advertised)
        if advertised is not None and energy is None:
            return self._reject("invalid_energy")
        if energy is not None:
            if not 0.0 <= energy <= 900.0:
                return self._reject("energy_out_of_range")
            if abs(energy - derived) > max(20.0, derived * 0.2):
                return self._reject("energy_mismatch")
        if require_energy and energy is None:
            return self._reject("energy_required")

        basis = data.get("metric_serving_amount", data.get("metric_basis_g"))
        if basis is None and require_metric_basis:
            return self._reject("metric_basis_required")
        if basis is not None:
            basis_value = _finite_float(basis)
            if basis_value is None or not 0.0 < basis_value <= MAX_FOOD_ITEM_QUANTITY:
                return self._reject("invalid_metric_basis")

        raw_options = data.get("allowed_units")
        if raw_options is None:
            raw_options = data.get("serving_sizes")
        options = normalize_serving_options(
            raw_options,
            provider_100g_label=provider_100g_label,
        )
        if options is None:
            return self._reject(
                "invalid_base_gram" if _has_bad_gram(raw_options) else "invalid_serving"
            )

        return NutritionIntegrityResult(
            accepted=True,
            reason_code=ACCEPTED_REASON,
            protein_100g=values["protein_100g"],
            carbs_100g=values["carbs_100g"],
            fat_100g=values["fat_100g"],
            fiber_100g=fiber,
            sugar_100g=sugar,
            calories_100g=energy,
            derived_calories_100g=derived,
            serving_options=tuple(options),
            origin=normalized_origin,
        )

    def require_valid(
        self, data: Mapping[str, Any], **kwargs: Any
    ) -> NutritionIntegrityResult:
        result = self.evaluate(data, **kwargs)
        if not result.accepted:
            raise NutritionIntegrityError(result)
        return result

    @staticmethod
    def rejection(reason_code: str) -> NutritionIntegrityResult:
        """Build a stable rejection for non-numeric identity checks."""
        return NutritionIntegrityResult(accepted=False, reason_code=reason_code)

    @staticmethod
    def _reject(reason_code: str) -> NutritionIntegrityResult:
        return NutritionIntegrityResult(accepted=False, reason_code=reason_code)

    @staticmethod
    def _display_result_without_macros(
        data: Mapping[str, Any],
        *,
        provider_100g_label: bool,
        origin: str | None,
    ) -> NutritionIntegrityResult:
        raw_options = data.get("allowed_units")
        if raw_options is None:
            raw_options = data.get("serving_sizes")
        options = normalize_serving_options(
            raw_options,
            provider_100g_label=provider_100g_label,
        ) or [dict(DEFAULT_GRAM_SERVING)]
        return NutritionIntegrityResult(
            accepted=True,
            reason_code=ACCEPTED_REASON,
            serving_options=tuple(options),
            origin=origin,
        )


def normalize_serving_options(
    raw_options: Any,
    *,
    provider_100g_label: bool = False,
) -> list[dict[str, Any]] | None:
    """Normalize serving conversions while reserving ``g`` for exactly one gram."""
    if raw_options is None:
        return [dict(DEFAULT_GRAM_SERVING)]
    if not isinstance(raw_options, list):
        return None

    normalized: list[dict[str, Any]] = []
    for raw in raw_options:
        if not isinstance(raw, Mapping):
            return None
        unit = str(
            raw.get("unit")
            or raw.get("measurement_description")
            or raw.get("name")
            or ""
        ).strip()
        grams = raw.get("gram_weight")
        if grams is None:
            grams = raw.get("grams", raw.get("metric_serving_amount"))
        gram_weight = _finite_float(grams)
        if (
            not unit
            or gram_weight is None
            or not 0.0 < gram_weight <= MAX_FOOD_ITEM_QUANTITY
        ):
            return None

        unit_key = unit.lower()
        description = str(
            raw.get("description") or raw.get("serving_description") or unit
        ).strip()
        if unit_key in _GRAM_UNITS:
            if math.isclose(gram_weight, 1.0):
                unit_key, gram_weight, description = "g", 1.0, "1 g"
            elif provider_100g_label:
                unit_key = "serving"
                description = description or f"{gram_weight:g} g"
            else:
                return None

        candidate = {
            "unit": unit_key,
            "gram_weight": float(gram_weight),
            "description": description[:100] or unit_key,
        }
        display = str(
            raw.get("display_description") or raw.get("name_vi") or ""
        ).strip()
        if display:
            candidate["display_description"] = display[:100]
        if not any(
            item["unit"] == candidate["unit"]
            and isinstance(item["gram_weight"], (int, float))
            and isinstance(candidate["gram_weight"], (int, float))
            and math.isclose(item["gram_weight"], candidate["gram_weight"])
            for item in normalized
        ):
            normalized.append(candidate)

    gram_items = [item for item in normalized if item["unit"] == "g"]
    if gram_items and not all(
        isinstance(item["gram_weight"], (int, float))
        and math.isclose(item["gram_weight"], 1.0)
        for item in gram_items
    ):
        return None
    normalized = [item for item in normalized if item["unit"] != "g"]
    return [dict(DEFAULT_GRAM_SERVING), *normalized]


@dataclass(frozen=True)
class LogicalOriginResult:
    accepted: bool
    reason_code: str
    origin: str | None = None


def normalize_logical_origin(fields: Mapping[str, Any]) -> LogicalOriginResult:
    """Reduce legacy identity fields to one low-cardinality logical origin."""
    origins: list[str] = []
    if fields.get("food_reference_id") is not None:
        origins.append("local")
    if fields.get("fdc_id") is not None:
        origins.append("usda")
    source = str(fields.get("source") or "").lower().strip()
    if fields.get("provider_food_id") is not None or (
        fields.get("food_id") is not None and source in _PROVIDER_SOURCES
    ):
        origins.append("provider")
    if fields.get("custom") is True or source == "custom":
        origins.append("custom")
    if fields.get("origin") is not None:
        explicit = str(fields["origin"]).lower().strip()
        if explicit in {"local", "usda", "provider", "custom"}:
            origins.append(explicit)
        else:
            return LogicalOriginResult(False, "invalid_origin")

    if len(origins) == 0:
        return LogicalOriginResult(False, "missing_origin")
    if len(set(origins)) != 1:
        return LogicalOriginResult(False, "multiple_origins")
    return LogicalOriginResult(True, ACCEPTED_REASON, origins[0])


def _finite_float(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _optional_finite_float(value: Any) -> float | None:
    return 0.0 if value is None else _finite_float(value)


def _has_bad_gram(raw_options: Any) -> bool:
    if not isinstance(raw_options, list):
        return False
    return any(
        isinstance(item, Mapping)
        and str(item.get("unit") or item.get("name") or "").lower().strip()
        in _GRAM_UNITS
        and not math.isclose(
            _finite_float(item.get("gram_weight", item.get("grams"))) or -1, 1.0
        )
        for item in raw_options
    )
