"""Calorie-formula parity characterization test (Wave 0, XC-CALORIE-SOT-01).

Locks current agreement between the canonical fiber-aware calorie formula
(`Macros.total_calories` / `meal_calorie_service._macro_calories`) and every
known Python/SQL duplicate before Wave 2 route-through/dedupe.

Canonical formula: P×4 + max(C−fiber, 0)×4 + fiber×2 + F×9

This is a CHARACTERIZATION test: it locks in *current* behavior, including
existing rounding-precision drift between sites (1dp / 2dp / int / unrounded).
Do not "fix" rounding here — any future dedupe (Wave 2) should update this
test deliberately, not silently.

Two verification strategies are used per site:
- DIRECT CALL: sites callable in isolation (pure functions, static methods,
  simple dataclasses/pydantic models) are invoked with real macro inputs and
  compared against the canonical raw (unrounded) value within a tolerance
  sized to that site's own rounding precision.
- STATIC SOURCE MATCH: sites embedded in code that requires heavy scaffolding
  to execute (FastAPI route handlers with DI, repository methods needing DB
  rows) or raw SQL (no DB fixture in unit tests) are verified by asserting
  the exact arithmetic literal is still present in source. This does not
  execute the code path, so it cannot catch every regression — documented
  limitation per Wave 0 scope.
"""

from decimal import Decimal

import pytest

from src.domain.model.nutrition.macros import Macros
from src.domain.services.meal_calorie_service import _macro_calories

# --- Macro fixtures (grams): zero-fiber, high-fiber, edge net_carbs=0, rounding boundary ---

MACRO_SETS = [
    pytest.param(30.0, 50.0, 20.0, 0.0, id="zero_fiber"),
    pytest.param(20.0, 40.0, 10.0, 15.0, id="high_fiber"),
    pytest.param(15.0, 10.0, 8.0, 15.0, id="edge_net_carbs_zero"),
    pytest.param(25.33, 45.67, 11.11, 2.22, id="rounding_boundary"),
]

# Max abs diff for sites that round to 1 decimal place. 0.05 is the true max
# rounding error; padded slightly for float-representation wobble at the
# x.x5 boundary (e.g. 495.55 not being exactly representable in binary).
_ONE_DP_TOL = 0.06


def _raw_formula(protein: float, carbs: float, fat: float, fiber: float) -> float:
    """Canonical unrounded fiber-aware formula, used as the parity baseline."""
    net_carbs = max(0.0, carbs - fiber)
    return protein * 4 + net_carbs * 4 + fiber * 2 + fat * 9


class TestCalorieFormulaParityDirectCall:
    """Sites invoked directly and compared to the raw canonical formula."""

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_sot_domain_model(self, protein, carbs, fat, fiber):
        raw = _raw_formula(protein, carbs, fat, fiber)
        result = Macros(
            protein=protein, carbs=carbs, fat=fat, fiber=fiber
        ).total_calories
        assert result == pytest.approx(raw, abs=_ONE_DP_TOL)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_sot_meal_calorie_service(self, protein, carbs, fat, fiber):
        raw = _raw_formula(protein, carbs, fat, fiber)
        macros = Macros(protein=protein, carbs=carbs, fat=fat, fiber=fiber)
        assert _macro_calories(macros) == pytest.approx(raw, abs=_ONE_DP_TOL)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_barcode_nutrition_validator(self, protein, carbs, fat, fiber):
        from src.domain.services.barcode.barcode_nutrition_validator import (
            _derive_calories,
        )

        raw = _raw_formula(protein, carbs, fat, fiber)
        assert _derive_calories(protein, carbs, fat, fiber) == pytest.approx(
            raw, abs=_ONE_DP_TOL
        )

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_food_mapping_service(self, protein, carbs, fat, fiber):
        from src.domain.services.food_mapping_service import FoodMappingService

        raw = _raw_formula(protein, carbs, fat, fiber)
        item = {
            "source": "food_reference",
            "food_reference_id": "fr-1",
            "is_verified": True,
            "protein_100g": protein,
            "carbs_100g": carbs,
            "fat_100g": fat,
            "fiber_100g": fiber,
        }
        result = FoodMappingService().map_search_item(item)
        assert result["calories"] == pytest.approx(raw, abs=1e-6)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_ingredient_quantity_conversion_service(self, protein, carbs, fat, fiber):
        from src.domain.ports.food_reference_repository_port import (
            FoodReferenceNutritionProjection,
        )
        from src.domain.services.meal_recommendation.ingredient_quantity_conversion_service import (
            IngredientQuantityConversionService,
        )

        raw = _raw_formula(protein, carbs, fat, fiber)
        reference = FoodReferenceNutritionProjection(
            id=1,
            name="test food",
            source="usda",
            is_verified=True,
            protein_100g=protein,
            carbs_100g=carbs,
            fat_100g=fat,
            fiber_100g=fiber,
        )
        resolved = IngredientQuantityConversionService().resolve(
            reference=reference, quantity=100.0, unit="g"
        )
        assert resolved.calories == pytest.approx(raw, abs=1e-6)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_catalog_recipe(self, protein, carbs, fat, fiber):
        from src.domain.model.meal_recommendation.catalog_recipe import CatalogMeal

        raw = _raw_formula(protein, carbs, fat, fiber)
        meal = CatalogMeal(
            id="meal-1",
            catalog_key="key-1",
            content_hash="hash-1",
            name="Test Meal",
            cuisine="test",
            description=None,
            image_url=None,
            protein_g=Decimal(str(protein)),
            carbs_g=Decimal(str(carbs)),
            fat_g=Decimal(str(fat)),
            fiber_g=Decimal(str(fiber)),
        )
        # Int-rounded property — widest tolerance among direct-call sites.
        assert meal.calories == pytest.approx(raw, abs=0.5)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_parse_meal_text_handler(self, protein, carbs, fat, fiber):
        from src.app.handlers.command_handlers.parse_meal_text_handler import (
            ParseMealTextHandler,
        )

        raw = _raw_formula(protein, carbs, fat, fiber)
        result = ParseMealTextHandler._derive_calories_from_macros(
            {"protein_g": protein, "carbs_g": carbs, "fat_g": fat, "fiber_g": fiber}
        )
        assert result == pytest.approx(raw, abs=0.005)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_nutrition_lookup_service(self, protein, carbs, fat, fiber):
        from src.domain.services.meal_suggestion.nutrition_lookup_service import (
            _derive_calories,
        )

        raw = _raw_formula(protein, carbs, fat, fiber)
        assert _derive_calories(protein, carbs, fat, fiber) == pytest.approx(
            raw, abs=1e-6
        )

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_macro_validation_service(self, protein, carbs, fat, fiber):
        from src.domain.services.meal_suggestion.macro_validation_service import (
            MacroValidationService,
        )

        raw = _raw_formula(protein, carbs, fat, fiber)
        # Reported calories deliberately implausible to force the service to
        # overwrite `calories` with its internally derived value.
        macros = {
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
            "fiber": fiber,
            "calories": 1.0,
        }
        result = MacroValidationService().validate_and_correct(macros)
        assert result["calories"] == pytest.approx(raw, abs=_ONE_DP_TOL)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_manual_meal_custom_nutrition_request(self, protein, carbs, fat, fiber):
        from src.api.schemas.request.meal_requests import (
            ManualMealCustomNutritionRequest,
        )

        raw = _raw_formula(protein, carbs, fat, fiber)
        request = ManualMealCustomNutritionRequest(
            protein_per_100g=protein,
            carbs_per_100g=carbs,
            fat_per_100g=fat,
            fiber_per_100g=fiber,
        )
        assert request.calories_per_100g == pytest.approx(raw, abs=0.005)

    @pytest.mark.parametrize("protein,carbs,fat,fiber", MACRO_SETS)
    def test_custom_nutrition_request(self, protein, carbs, fat, fiber):
        from src.api.schemas.request.meal_requests import CustomNutritionRequest

        raw = _raw_formula(protein, carbs, fat, fiber)
        request = CustomNutritionRequest(
            protein_per_100g=protein,
            carbs_per_100g=carbs,
            fat_per_100g=fat,
            fiber_per_100g=fiber,
        )
        assert request.calories_per_100g == pytest.approx(raw, abs=0.005)

    def test_parse_text_api_mapper_propagates_fiber_sugar_and_derived_calories(self):
        """Expected red: parse-text mapper derives fiber-aware calories but drops fields."""
        from types import SimpleNamespace

        from src.api.routes.v1.meals_route_helpers import parsed_food_item_to_response

        item = parsed_food_item_to_response(
            SimpleNamespace(
                name="bran cereal",
                quantity=100.0,
                unit="g",
                protein=15.0,
                carbs=40.0,
                fat=10.0,
                fiber=8.0,
                sugar=12.0,
                data_source="fatsecret",
                fdc_id=None,
                allowed_units=[],
            )
        )

        assert item.fiber == 8.0
        assert item.sugar == 12.0
        assert item.calories == pytest.approx(294.0)


class TestCalorieFormulaParityStaticSource:
    """Sites too heavy to invoke in a unit test (route DI, DB-bound repo
    methods, raw SQL with no DB fixture). Verified via static source-text
    match instead of execution — see module docstring for the limitation.

    Wave 2 (item 14) routed these Python sites through
    ``Macros.raw_total_calories`` and consolidated the SQL literal into
    ``CALORIE_FORMULA_SQL_FRAGMENT`` — assertions below check for the shared
    call/import instead of the formerly-inline arithmetic/literal.
    """

    def test_meal_suggestions_route_delegates_to_sot(self):
        path = "src/api/routes/v1/meal_suggestions.py"
        with open(path) as f:
            source = f.read()
        assert "from src.domain.model.nutrition.macros import Macros" in source
        assert "Macros.raw_total_calories(body.protein" in source

    def test_meal_repository_async_delegates_to_sot(self):
        path = "src/infra/repositories/meal_repository_async.py"
        with open(path) as f:
            source = f.read()
        assert "from src.domain.model.nutrition.macros import Macros" in source
        assert "Macros.raw_total_calories(protein, carbs, fat, fiber)" in source


    def test_calorie_formula_sql_fragment_matches_python_sot(self):
        """Locks the shared SQL fragment's literal text (the SQL-side SoT)."""
        from src.domain.constants.calorie_sql import CALORIE_FORMULA_SQL_FRAGMENT

        assert "n.protein * 4.0" in CALORIE_FORMULA_SQL_FRAGMENT
        assert "GREATEST(n.carbs - n.fiber, 0) * 4.0" in CALORIE_FORMULA_SQL_FRAGMENT
        assert "n.fiber * 2.0" in CALORIE_FORMULA_SQL_FRAGMENT
        assert "n.fat * 9.0" in CALORIE_FORMULA_SQL_FRAGMENT
