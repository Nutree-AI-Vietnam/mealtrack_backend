import pytest

from src.planner_feature_flags import (
    CATALOG_DURABLE_PREPARATION,
    CATALOG_PROJECTIONS,
    CATALOG_PUBLICATION_FENCING,
    WEEKLY_PLANNER_SHORT_GENERATION,
)


@pytest.fixture
def planner_flags_off(monkeypatch):
    """Exercise the flag-off paths that remain available as kill switches."""
    for flag in (
        CATALOG_PROJECTIONS,
        CATALOG_PUBLICATION_FENCING,
        WEEKLY_PLANNER_SHORT_GENERATION,
        CATALOG_DURABLE_PREPARATION,
    ):
        monkeypatch.setenv(flag, "false")
