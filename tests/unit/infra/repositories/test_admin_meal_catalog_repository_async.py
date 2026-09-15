from types import SimpleNamespace

import pytest

from src.infra.repositories.admin_meal_catalog_repository_async import (
    AsyncAdminMealCatalogRepository,
)


class _Session:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(rowcount=1)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_set_missing_image_url_bumps_updated_at():
    session = _Session()
    repo = AsyncAdminMealCatalogRepository(session)

    persisted = await repo.set_missing_image_url(
        "catalog-1", "https://image.test/pho.jpg"
    )

    assert persisted is True
    compiled = session.statement.compile()
    assert "updated_at" in str(compiled)
    assert compiled.params["image_url"] == "https://image.test/pho.jpg"
