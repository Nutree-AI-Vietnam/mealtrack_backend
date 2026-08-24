"""
Maps USDA FDC responses into internal simplified structures and domain-friendly dictionaries.
Keeps logic flat and readable.
"""

from typing import Any

USDA_NUTRIENT_MAPPING = {
    1008: "calories",  # Energy (cal)
    1003: "protein",  # Protein (g)
    1005: "carbs",  # Carbohydrate (g)
    1004: "fat",  # Total lipid (fat) (g)
    1079: "fiber",  # Fiber, total dietary (g)
    2000: "sugar",  # Sugars, total including NLEA (g)
}

# Fallback unit categories for custom/manual ingredients
FALLBACK_UNIT_CATEGORIES = {
    "solid": [
        {"unit": "g", "gram_weight": 1.0},
        {"unit": "kg", "gram_weight": 1000.0},
        {"unit": "oz", "gram_weight": 28.35},
        {"unit": "lb", "gram_weight": 453.6},
    ],
    "liquid": [
        {"unit": "ml", "gram_weight": 1.0},
        {"unit": "l", "gram_weight": 1000.0},
        {"unit": "cup", "gram_weight": 240.0},
        {"unit": "tbsp", "gram_weight": 15.0},
        {"unit": "tsp", "gram_weight": 5.0},
    ],
    "countable": [
        {"unit": "piece", "gram_weight": 1.0},
        {"unit": "g", "gram_weight": 1.0},
        {"unit": "oz", "gram_weight": 28.35},
    ],
    "powder": [
        {"unit": "g", "gram_weight": 1.0},
        {"unit": "tbsp", "gram_weight": 8.0},
        {"unit": "tsp", "gram_weight": 3.0},
        {"unit": "cup", "gram_weight": 120.0},
    ],
}

DEFAULT_ALLOWED_UNITS = [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]


def _namespaced_id(namespace: Any, source_id: Any) -> str:
    """Return the stable display alias while keeping the source id opaque."""
    normalized_namespace = str(namespace).strip()
    normalized_id = str(source_id).strip()
    prefix = f"{normalized_namespace}:"
    if normalized_id.startswith(prefix):
        normalized_id = normalized_id[len(prefix) :]
    return f"{normalized_namespace}:{normalized_id}"


def _opaque_source_id(namespace: Any, source_id: Any) -> str:
    normalized_namespace = str(namespace).strip()
    normalized_id = str(source_id).strip()
    prefix = f"{normalized_namespace}:"
    return (
        normalized_id[len(prefix) :]
        if normalized_id.startswith(prefix)
        else normalized_id
    )


def _catalog_identity_for_adopted_hit(item: dict[str, Any]) -> dict[str, Any]:
    """Adopted FatSecret hits keep provider ids; catalog id is the save origin."""
    adopted_id = item.get("food_reference_id")
    if item.get("source") != "fatsecret" or adopted_id is None:
        return item
    return {
        **item,
        "source": "food_reference",
        "origin": "local",
        "is_verified": True,
        "food_id": f"food_reference:{adopted_id}",
    }


from src.domain.ports.food_mapping_service_port import FoodMappingServicePort
from src.domain.services.nutrition_integrity_policy import (
    NutritionIntegrityError,
    NutritionIntegrityPolicy,
    normalize_serving_options,
)


class FoodMappingService(FoodMappingServicePort):
    def __init__(self, integrity_policy: NutritionIntegrityPolicy | None = None):
        self._integrity_policy = integrity_policy or NutritionIntegrityPolicy()

    def map_search_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item = _catalog_identity_for_adopted_hit(item)
        if item.get("source") == "food_reference":
            if item.get("is_verified") is not True:
                raise NutritionIntegrityError(
                    self._integrity_policy.rejection("unverified_reference")
                )
            food_reference_id = item.get("food_reference_id")
            expected_alias = f"food_reference:{food_reference_id}"
            supplied_alias = item.get("food_id")
            if supplied_alias is not None and str(supplied_alias) != expected_alias:
                raise NutritionIntegrityError(
                    self._integrity_policy.rejection("origin_alias_mismatch")
                )
            result = self._require_search_integrity(
                item,
                require_energy=False,
                require_origin=True,
            )
            protein = result.protein_100g
            carbs = result.carbs_100g
            fat = result.fat_100g
            fiber = result.fiber_100g
            calories = result.derived_calories_100g
            source_namespace = str(item.get("source_namespace") or "").strip()
            source_food_id = item.get("source_food_id")
            if not source_namespace or source_namespace == "food_reference":
                source_namespace = "food_reference"
                source_food_id = str(food_reference_id)
            else:
                source_food_id = str(source_food_id or food_reference_id)
            return {
                "fdc_id": None,
                "food_id": expected_alias,
                "food_reference_id": food_reference_id,
                "origin": "local",
                "source_namespace": source_namespace,
                "source_food_id": source_food_id,
                "name": item.get("description"),
                "brand": item.get("brand"),
                "data_type": "food_reference",
                "serving_size": item.get("serving_description"),
                "serving_unit": "g",
                "calories": calories,
                "nutrients": {
                    "protein": protein,
                    "fat": fat,
                    "carbs": carbs,
                    "fiber": fiber,
                    "sugar": result.sugar_100g,
                },
                "source": "food_reference",
                "provider_source": item.get("provider_source"),
                "is_verified": item.get("is_verified"),
                "allowed_units": list(result.serving_options),
                "custom_nutrition": (
                    {
                        "calories_per_100g": calories,
                        "protein_per_100g": protein,
                        "carbs_per_100g": carbs,
                        "fat_per_100g": fat,
                    }
                    if protein is not None and carbs is not None and fat is not None
                    else None
                ),
                "nutrition_basis": "100g",
                "nutrition_contract_version": result.policy_version,
                "calories_per_100g": calories,
            }

        # Handle fatsecret results with embedded nutrition
        if item.get("source") == "fatsecret":
            result = self._require_search_integrity(
                item,
                provider=True,
                require_origin=True,
            )
            return {
                "fdc_id": None,  # fatsecret doesn't use FDC IDs
                "food_id": _namespaced_id(
                    item.get("source_namespace") or "fatsecret",
                    item.get("source_food_id") or item.get("food_id"),
                ),
                "origin": "provider",
                "source_namespace": item.get("source_namespace") or "fatsecret",
                "source_food_id": _opaque_source_id(
                    item.get("source_namespace") or "fatsecret",
                    item.get("source_food_id") or item.get("food_id"),
                ),
                "name": item.get("description"),
                "brand": item.get("brand"),
                "data_type": "fatsecret",
                "serving_size": item.get("serving_description"),
                "serving_unit": "g",
                "calories": result.derived_calories_100g,
                "nutrients": {
                    "protein": result.protein_100g,
                    "fat": result.fat_100g,
                    "carbs": result.carbs_100g,
                    "fiber": result.fiber_100g,
                    "sugar": result.sugar_100g,
                },
                "source": "fatsecret",
                "allowed_units": list(result.serving_options),
                # Include custom nutrition for manual meal creation
                "custom_nutrition": (
                    {
                        "calories_per_100g": result.derived_calories_100g,
                        "protein_per_100g": result.protein_100g,
                        "carbs_per_100g": result.carbs_100g,
                        "fat_per_100g": result.fat_100g,
                    }
                    if result.calories_100g is not None
                    else None
                ),
                "nutrition_basis": "100g",
                "nutrition_contract_version": result.policy_version,
                "calories_per_100g": result.derived_calories_100g,
            }

        # USDA results
        nutrients = self._extract_macros(item.get("foodNutrients") or [])

        result = {
            "fdc_id": item.get("fdcId"),
            "name": item.get("description"),
            "brand": item.get("brandOwner"),
            "data_type": item.get("dataType"),
            "published_date": item.get("publishedDate"),
            "serving_size": item.get("servingSize"),
            "serving_unit": item.get("servingSizeUnit"),
            "calories": nutrients.get("calories"),
            "nutrients": {
                "protein": nutrients.get("protein"),
                "fat": nutrients.get("fat"),
                "carbs": nutrients.get("carbs"),
                "fiber": nutrients.get("fiber"),
                "sugar": nutrients.get("sugar"),
            },
            "allowed_units": (
                self._parse_usda_portions(item.get("foodPortions"))
                if item.get("foodPortions")
                else DEFAULT_ALLOWED_UNITS
            ),
        }
        integrity = self._require_search_integrity(
            {
                "protein_100g": nutrients.get("protein"),
                "carbs_100g": nutrients.get("carbs"),
                "fat_100g": nutrients.get("fat"),
                "fiber_100g": nutrients.get("fiber"),
                "sugar_100g": nutrients.get("sugar"),
                "calories_100g": nutrients.get("calories"),
                "allowed_units": result["allowed_units"],
                "source": item.get("source", "usda"),
                "fdc_id": item.get("fdcId"),
            },
            provider=True,
            require_origin=True,
        )
        result["calories"] = integrity.derived_calories_100g
        result["allowed_units"] = list(integrity.serving_options)
        result["origin"] = "usda"
        result["source_namespace"] = "usda_fdc"
        result["source_food_id"] = str(item.get("fdcId"))
        result["food_id"] = _namespaced_id("usda_fdc", item.get("fdcId"))
        result["nutrition_basis"] = "100g"
        result["nutrition_contract_version"] = integrity.policy_version
        result["calories_per_100g"] = integrity.derived_calories_100g
        if "source" in item:
            result["source"] = item["source"]
        return result

    def _parse_usda_portions(
        self, portions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Parse USDA foodPortions into allowed_units format."""
        if not portions:
            return DEFAULT_ALLOWED_UNITS

        units = []
        for portion in portions:
            measure = portion.get("measureUnit", {})
            unit_name = measure.get("name") or measure.get("abbreviation") or "portion"
            gram_weight = portion.get("gramWeight")
            description = portion.get("portionDescription", "")

            if gram_weight and float(gram_weight) > 0:
                units.append(
                    {
                        "unit": unit_name,
                        "gram_weight": float(gram_weight),
                        "description": description,
                    }
                )

        if not units:
            return DEFAULT_ALLOWED_UNITS

        return (
            normalize_serving_options(units, provider_100g_label=True)
            or DEFAULT_ALLOWED_UNITS
        )

    def _require_search_integrity(
        self,
        item: dict[str, Any],
        *,
        provider: bool = False,
        require_energy: bool = True,
        require_origin: bool = False,
    ):
        result = self._integrity_policy.evaluate(
            item,
            require_energy=require_energy,
            require_metric_basis=False,
            provider_100g_label=provider,
            origin_fields=item if require_origin else None,
            require_macros=False,
        )
        if not result.accepted:
            raise NutritionIntegrityError(result)
        return result

    def _extract_macros(self, nutrients: list[dict[str, Any]]) -> dict[str, float]:
        values: dict[str, float] = {
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "fiber": 0.0,
            "sugar": 0.0,
        }
        for entry in nutrients or []:
            # Handle both search results format and details format
            if "nutrient" in entry:
                # Details format: nested structure
                nutrient = entry.get("nutrient") or {}
                nutrient_id = nutrient.get("id")
                amount = float(entry.get("amount") or 0.0)
            else:
                # Search results format: flat structure
                nutrient_id = entry.get("nutrientId")
                amount = float(entry.get("value") or 0.0)

            key = USDA_NUTRIENT_MAPPING.get(nutrient_id)
            if not key:
                key = self._nutrient_name_key(entry)
            if key:
                values[key] = amount
        return values

    @staticmethod
    def _nutrient_name_key(entry: dict[str, Any]) -> str | None:
        nutrient = entry.get("nutrient") or {}
        name = str(entry.get("nutrientName") or nutrient.get("name") or "").lower()
        if "protein" in name:
            return "protein"
        if "carbohydrate" in name:
            return "carbs"
        if "total lipid" in name or name == "fat":
            return "fat"
        if "fiber" in name:
            return "fiber"
        if "sugar" in name:
            return "sugar"
        return None

    def map_fdc_barcode_product(
        self, item: dict[str, Any], barcode: str
    ) -> dict[str, Any]:
        nutrients = self._extract_macros(item.get("foodNutrients") or [])
        serving_size = item.get("servingSize")
        serving_unit = item.get("servingSizeUnit")
        serving_text = None
        if serving_size and serving_unit:
            serving_text = f"{serving_size} {serving_unit}"
        elif serving_size:
            serving_text = str(serving_size)

        return {
            "name": item.get("description") or item.get("lowercaseDescription"),
            "brand": item.get("brandOwner") or item.get("brandName"),
            "barcode": barcode,
            "protein_100g": nutrients.get("protein", 0.0),
            "carbs_100g": nutrients.get("carbs", 0.0),
            "fat_100g": nutrients.get("fat", 0.0),
            "fiber_100g": nutrients.get("fiber", 0.0),
            "sugar_100g": nutrients.get("sugar", 0.0),
            "serving_size": serving_text,
            "fdc_id": item.get("fdcId"),
            "source": "usda_fdc",
            "provider_source": "usda_fdc",
            "source_namespace": "usda_fdc",
            "source_food_id": (
                str(item["fdcId"]) if item.get("fdcId") is not None else None
            ),
            "is_verified": True,
            "is_estimate": False,
            "allowed_units": self._barcode_allowed_units(
                item, serving_size, serving_unit
            ),
        }

    def _barcode_allowed_units(
        self,
        item: dict[str, Any],
        serving_size: Any,
        serving_unit: Any,
    ) -> list[dict[str, Any]]:
        if item.get("foodPortions"):
            return self._parse_usda_portions(item.get("foodPortions"))
        if not serving_size:
            return DEFAULT_ALLOWED_UNITS
        try:
            grams = float(serving_size)
        except (TypeError, ValueError):
            return DEFAULT_ALLOWED_UNITS
        if grams <= 0 or str(serving_unit or "").lower() not in {
            "g",
            "gram",
            "grams",
        }:
            return DEFAULT_ALLOWED_UNITS
        return [
            {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            {
                "unit": "serving",
                "gram_weight": grams,
                "description": f"1 serving ({serving_size} {serving_unit})",
            },
        ]

    def map_food_details(self, details: dict[str, Any]) -> dict[str, Any]:
        macros = self._extract_macros(details.get("foodNutrients") or [])
        return {
            "fdc_id": details.get("fdcId"),
            "name": details.get("description"),
            "brand": details.get("brandOwner"),
            "serving_size": details.get("servingSize"),
            "serving_unit": details.get("servingSizeUnit"),
            "calories": macros.get("calories"),
            "macros": {
                "protein": macros.get("protein"),
                "carbs": macros.get("carbs"),
                "fat": macros.get("fat"),
            },
            "portions": details.get("foodPortions") or [],
            "allowed_units": (
                self._parse_usda_portions(details.get("foodPortions"))
                if details.get("foodPortions")
                else DEFAULT_ALLOWED_UNITS
            ),
        }
