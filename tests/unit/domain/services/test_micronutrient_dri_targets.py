from src.domain.services.micronutrient_dri_targets import micronutrient_daily_targets


def test_adult_male_uses_iron_8_and_potassium_3400():
    t = micronutrient_daily_targets(
        gender="male", age_years=34, plan_calories=1947
    )
    assert t.iron_mg == 8.0
    assert t.potassium_mg == 3400.0
    assert t.sodium_mg == 2300.0
    assert t.fiber_g == 27.3
    assert t.added_sugar_g == 48.7


def test_adult_female_uses_iron_18_and_potassium_2600():
    t = micronutrient_daily_targets(
        gender="female", age_years=28, plan_calories=2000
    )
    assert t.iron_mg == 18.0
    assert t.potassium_mg == 2600.0
    assert t.fiber_g == 28.0
    assert t.added_sugar_g == 50.0


def test_postmenopausal_female_iron_drops_to_8():
    t = micronutrient_daily_targets(
        gender="female", age_years=55, plan_calories=1800
    )
    assert t.iron_mg == 8.0
    assert t.potassium_mg == 2600.0


def test_unknown_sex_uses_female_iron_band():
    t = micronutrient_daily_targets(
        gender="other", age_years=30, plan_calories=2000
    )
    assert t.iron_mg == 18.0
    assert t.potassium_mg == 2600.0


def test_missing_profile_defaults_to_adult_female_band():
    t = micronutrient_daily_targets(
        gender=None, age_years=None, plan_calories=0
    )
    assert t.iron_mg == 18.0
    assert t.potassium_mg == 2600.0
    assert t.sodium_mg == 2300.0
    assert t.fiber_g == 25.0
    assert t.added_sugar_g == 50.0
