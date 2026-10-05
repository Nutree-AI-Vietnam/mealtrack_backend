import pytest

from src.planner_feature_flags import CATALOG_PROJECTIONS, planner_flag_enabled


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", " Yes "])
def test_truthy_values_enable_flag(monkeypatch, value):
    monkeypatch.setenv(CATALOG_PROJECTIONS, value)
    assert planner_flag_enabled(CATALOG_PROJECTIONS)


@pytest.mark.parametrize("value", ["false", "0", "no", "", "enabled"])
def test_other_values_disable_flag(monkeypatch, value):
    monkeypatch.setenv(CATALOG_PROJECTIONS, value)
    assert not planner_flag_enabled(CATALOG_PROJECTIONS)


def test_missing_flag_is_enabled(monkeypatch):
    monkeypatch.delenv(CATALOG_PROJECTIONS, raising=False)
    assert planner_flag_enabled(CATALOG_PROJECTIONS)
