"""21-key scan contracts: chosen only with full micros on, always parsed."""

import pytest

from src.domain.model.ai.full_micros_contracts import (
    FULL_MICROS_ADDED_KEYS,
    AINutrientMicrosFull,
    FoodLabelNutritionResponseFull,
    MealTextNutritionResponseFull,
    VisionNutritionResponseFull,
    scan_schema,
)
from src.domain.model.ai.nutrition_contracts import (
    AINutrientMicros,
    FoodLabelNutritionResponse,
    MealTextNutritionResponse,
    VisionNutritionResponse,
)
from src.domain.model.nutrition.micros import Micros
from src.domain.parsers.vision_response_parser import VisionResponseParser
from src.infra.services.ai.langchain_openai_adapter import _openai_json_schema

_MACROS = {"protein_g": 31, "carbs_g": 0, "fat_g": 3.6, "fiber_g": 0, "sugar_g": 0}
_NEW_VALUES = {"vitamin_d": 0.1, "niacin": 13.7, "selenium": 27.6, "zinc": 1.0}


@pytest.mark.parametrize(
    ("base", "full"),
    [
        (VisionNutritionResponse, VisionNutritionResponseFull),
        (FoodLabelNutritionResponse, FoodLabelNutritionResponseFull),
        (MealTextNutritionResponse, MealTextNutritionResponseFull),
    ],
)
def test_flag_off_keeps_todays_contract_and_on_picks_the_wide_one(base, full):
    assert scan_schema(base, full_micros=False) is base
    assert scan_schema(base, full_micros=True) is full


def test_the_wide_micros_hold_every_micros_key():
    wide = set(AINutrientMicrosFull.model_fields)
    assert wide == set(AINutrientMicros.model_fields) | set(FULL_MICROS_ADDED_KEYS)
    # Every scan key is a Micros field, so storage keeps it.
    assert wide == set(Micros.__dataclass_fields__)


def _required_micros(schema) -> list[str]:
    defs = _openai_json_schema(schema)["schema"]["$defs"]
    micros = next(d for name, d in defs.items() if name.startswith("AINutrientMicros"))
    return micros["required"]


def test_provider_is_asked_for_ten_keys_off_and_21_on():
    assert len(_required_micros(VisionNutritionResponse)) == 10
    assert len(_required_micros(VisionNutritionResponseFull)) == 21
    assert len(_required_micros(FoodLabelNutritionResponseFull)) == 21
    assert len(_required_micros(MealTextNutritionResponseFull)) == 21


def test_text_contract_parses_with_and_without_the_new_keys():
    item = {"name": "chicken", "quantity": 100, "unit": "g", "macros": _MACROS}
    ten = MealTextNutritionResponseFull.model_validate(
        {"items": [{**item, "micros": {"iron": 1.1}}]}
    )
    assert ten.items[0].micros.vitamin_d is None
    wide = MealTextNutritionResponseFull.model_validate(
        {"items": [{**item, "micros": {"iron": 1.1, **_NEW_VALUES}}]}
    )
    assert wide.items[0].micros.niacin == 13.7
    # Today's contract still drops what it never asked for.
    narrow = MealTextNutritionResponse.model_validate(
        {"items": [{**item, "micros": {"iron": 1.1, **_NEW_VALUES}}]}
    )
    assert not hasattr(narrow.items[0].micros, "niacin")


def test_label_contract_reads_printed_vitamins():
    label = FoodLabelNutritionResponseFull.model_validate(
        {
            "macros_per_serving": _MACROS,
            "micros_per_serving": {"vitamin_b12": 0.6, "folate": 40},
        }
    )
    assert label.micros_per_serving.vitamin_b12 == 0.6


def test_scan_parser_keeps_new_keys_and_leaves_unknown_ones_absent():
    nutrition = VisionResponseParser().parse_to_nutrition(
        {
            "structured_data": {
                "dish_name": "chicken",
                "foods": [
                    {
                        "name": "chicken breast",
                        "quantity_g": 100,
                        "macros": _MACROS,
                        "micros": {"iron": 1.1, **_NEW_VALUES, "folate": None},
                    }
                ],
                "confidence": 0.9,
            }
        }
    )
    micros = nutrition.food_items[0].micros.to_dict()
    assert micros == {"iron": 1.1, **_NEW_VALUES}
    assert "folate" not in micros
