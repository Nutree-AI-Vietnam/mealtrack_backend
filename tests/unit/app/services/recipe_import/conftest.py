import pytest
from tests.fixtures.fakes.fake_recipe_import import FakeNutrition


@pytest.fixture
def nutrition() -> FakeNutrition:
    return FakeNutrition()
