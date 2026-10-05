from types import SimpleNamespace

import pytest

from src.app.services.catalog_persisted_presentation_service import (
    CatalogPersistedPresentationService,
)


class _Uow:
    def __init__(self, calls):
        self.calls = calls
        self.food_references = SimpleNamespace(
            get_display_projections=self._projections
        )
        self.catalog_preparation = SimpleNamespace(
            load_ingredient_translations=self._prepared
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _projections(self, ids, language):
        self.calls.append(("projections", tuple(ids)))
        return {1: {"name": "Rice", "name_vi": "Gạo"}, 2: {"name": "Broccoli"}}

    async def _prepared(self, ids, locale):
        self.calls.append(("prepared", tuple(ids), locale))
        return {1: "Cơm", 2: "Bông cải xanh"}


def _categories():
    items = (
        SimpleNamespace(ingredient_id=1, name="Rice"),
        SimpleNamespace(ingredient_id=2, name="Broccoli"),
        SimpleNamespace(ingredient_id=3, name="Salt"),
    )
    return (SimpleNamespace(items=items),)


@pytest.mark.asyncio
async def test_grocery_names_prefer_authored_vietnamese_then_prepared_overlay():
    calls = []
    service = CatalogPersistedPresentationService(lambda: _Uow(calls))

    assert await service.get_grocery_translations(_categories(), "vi") == {
        "Rice": "Gạo",
        "Broccoli": "Bông cải xanh",
    }


@pytest.mark.asyncio
async def test_grocery_names_use_prepared_overlay_for_other_locales():
    calls = []
    service = CatalogPersistedPresentationService(lambda: _Uow(calls))

    assert await service.get_grocery_translations(_categories(), "fr") == {
        "Rice": "Cơm",
        "Broccoli": "Bông cải xanh",
    }
    assert calls == [("prepared", (1, 2, 3), "fr")]


@pytest.mark.asyncio
async def test_english_grocery_names_skip_database_reads():
    calls = []
    service = CatalogPersistedPresentationService(lambda: _Uow(calls))

    assert await service.get_grocery_translations(_categories(), "en") == {}
    assert calls == []
