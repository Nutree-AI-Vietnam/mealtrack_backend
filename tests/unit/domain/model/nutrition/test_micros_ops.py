from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros_ops import merge_micros, scale_micros


def test_extra_nutrients_aliases_and_nested_amount():
    micros = extra_nutrients_to_micros(
        {
            "calcium_mg": 100,
            "sodium_mg": {"amount": 200, "unit": "mg"},
            "saturated_fat": 3,
        },
        factor=0.5,
    )
    assert micros is not None
    assert micros.calcium == 50
    assert micros.sodium == 100
    assert micros.saturated_fat == 1.5


def test_merge_and_scale_skip_empty():
    assert merge_micros(None, None) is None
    left = extra_nutrients_to_micros({"iron_mg": 2})
    right = extra_nutrients_to_micros({"iron_mg": 3, "vitamin_c_mg": 10})
    merged = merge_micros(left, right)
    assert merged is not None
    assert merged.iron == 5
    assert merged.vitamin_c == 10
    scaled = scale_micros(merged, 2)
    assert scaled is not None
    assert scaled.iron == 10
