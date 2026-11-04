from src.domain.model.nutrition.upper_limits import UpperLimitScope, upper_limits_for


def test_woman_30_limits_from_the_vietnam_book():
    limits = upper_limits_for(30, male=False)
    assert limits["vitamin_a"].value == 2700
    assert limits["vitamin_a"].scope is UpperLimitScope.PREFORMED_ONLY
    assert limits["iron"].value == 45
    assert limits["selenium"].value == 350
    assert limits["calcium"].value == 2500
    assert limits["phosphorus"].value == 3000


def test_us_limits_fill_in_where_the_book_has_none():
    limits = upper_limits_for(30, male=False)
    assert limits["vitamin_c"].value == 2000
    assert limits["vitamin_c"].scope is UpperLimitScope.ALL_SOURCES
    assert limits["zinc"].value == 40
    assert limits["magnesium"].value == 350
    assert limits["magnesium"].scope is UpperLimitScope.SUPPLEMENTS_ONLY


def test_supplement_only_limits_never_count_food():
    limits = upper_limits_for(30, male=True)
    for key in ("magnesium", "niacin", "folate", "vitamin_e"):
        assert limits[key].scope is UpperLimitScope.SUPPLEMENTS_ONLY, key


def test_no_limit_for_k_b1_b2_b12_and_potassium():
    limits = upper_limits_for(30, male=True)
    for key in ("vitamin_k", "thiamin", "riboflavin", "vitamin_b12", "potassium"):
        assert key not in limits


def test_men_get_their_own_selenium_and_vitamin_e_limits():
    limits = upper_limits_for(30, male=True)
    assert limits["selenium"].value == 460
    assert limits["vitamin_e"].value == 900


def test_teens_have_lower_limits_and_no_phosphorus_limit():
    limits = upper_limits_for(16, male=False)
    assert limits["vitamin_a"].value == 2600
    assert limits["zinc"].value == 34
    assert "phosphorus" not in limits
