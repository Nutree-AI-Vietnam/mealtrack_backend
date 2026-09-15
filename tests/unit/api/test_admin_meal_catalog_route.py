from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import (
    get_admin_meal_catalog_repository,
    get_catalog_image_generator_factory,
)
from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1 import admin_meal_catalog as route_mod
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.infra.repositories.admin_meal_catalog_repository_async import (
    AdminCatalogMealPage,
    AdminCatalogMealProjection,
)


def test_list_admin_meal_catalog_returns_frontend_contract(monkeypatch):
    repository = _Repository(page_items=(_projection(),))
    client = _client(repository)

    response = client.get(
        "/v1/admin/meal-catalog",
        params={
            "limit": 25,
            "offset": 0,
            "q": "pho",
            "cuisine": "vietnamese",
            "meal_type": "lunch",
            "has_image": False,
            "is_active": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 25
    assert payload["offset"] == 0
    item = payload["items"][0]
    assert item["catalog_key"] == "pho-ga"
    assert item["meal_types"] == ["lunch", "dinner"]
    assert item["calories"] == 455
    assert item["ingredient_count"] == 1
    assert item["ingredients"] == [
        {"display_name": "Chicken breast", "quantity": 120.0, "unit": "g"}
    ]
    assert repository.list_kwargs["has_image"] is False
    assert repository.list_kwargs["is_active"] is True


def test_list_admin_meal_catalog_rejects_invalid_meal_type(monkeypatch):
    client = _client(_Repository())

    response = client.get(
        "/v1/admin/meal-catalog",
        params={"limit": 25, "offset": 0, "meal_type": "brunch"},
    )

    assert response.status_code == 422


def test_generate_image_updates_missing_image_meal(monkeypatch):
    repository = _Repository(row=_row())
    generator = SimpleNamespace(
        generate_url=AsyncMock(return_value="https://img.test/pho.jpg")
    )
    client = _client(repository, generator=generator)

    response = client.post("/v1/admin/meal-catalog/catalog-1/generate-image")

    assert response.status_code == 200
    payload = response.json()
    assert payload["image_url"] == "https://img.test/pho.jpg"
    assert payload["item"]["image_url"] == "https://img.test/pho.jpg"
    assert repository.saved_image_url == "https://img.test/pho.jpg"
    generator.generate_url.assert_awaited_once()


def test_generate_image_rejects_when_another_request_wrote_first(monkeypatch):
    repository = _Repository(row=_row(), write_succeeds=False)
    generator = SimpleNamespace(
        generate_url=AsyncMock(return_value="https://img.test/pho.jpg")
    )
    client = _client(repository, generator=generator)

    response = client.post("/v1/admin/meal-catalog/catalog-1/generate-image")

    assert response.status_code == 409
    assert repository.saved_image_url is None
    generator.generate_url.assert_awaited_once()


def test_generate_image_rejects_unknown_catalog_id(monkeypatch):
    client = _client(_Repository(row=None))

    response = client.post("/v1/admin/meal-catalog/missing/generate-image")

    assert response.status_code == 404


def test_generate_image_rejects_existing_image(monkeypatch):
    client = _client(
        _Repository(row=_row(image_url="https://old.test/x.jpg")),
    )

    response = client.post("/v1/admin/meal-catalog/catalog-1/generate-image")

    assert response.status_code == 409


def test_admin_gate_is_required_for_catalog_view(monkeypatch):
    from src.api.dependencies import auth as auth_dep

    async def fake_verify_firebase_token(request, credentials):
        return {"email": "reader@example.com"}

    monkeypatch.setattr(auth_dep.settings, "ADMIN_EMAILS", "")
    monkeypatch.setattr(auth_dep, "verify_firebase_token", fake_verify_firebase_token)
    app = FastAPI()
    app.include_router(route_mod.router)
    app.dependency_overrides[get_admin_meal_catalog_repository] = lambda: _Repository()

    response = TestClient(app).get(
        "/v1/admin/meal-catalog?limit=25&offset=0",
        headers={"Authorization": "Bearer fake-token"},
    )

    assert response.status_code == 403


def test_local_development_allows_catalog_view_without_admin(monkeypatch):
    repository = _Repository(page_items=(_projection(),))
    client = _client(repository, use_route_auth=True)

    response = client.get("/v1/admin/meal-catalog?limit=25&offset=0")

    assert response.status_code == 200
    assert response.json()["items"][0]["catalog_key"] == "pho-ga"


class _Repository:
    def __init__(self, *, page_items=(), row=None, write_succeeds=True):
        self.page_items = page_items
        self.row = row
        self.write_succeeds = write_succeeds
        self.list_kwargs = {}
        self.saved_image_url = None
        self.commit = AsyncMock()

    async def list_meals(self, **kwargs):
        self.list_kwargs = kwargs
        return AdminCatalogMealPage(
            items=tuple(self.page_items), total=len(self.page_items)
        )

    async def get_meal_row(self, catalog_id):
        return self.row

    async def set_missing_image_url(self, catalog_id, image_url):
        if not self.write_succeeds:
            return False
        self.saved_image_url = image_url
        if self.row is not None:
            self.row.image_url = image_url
        return True

    async def get_meal(self, catalog_id):
        return _projection(image_url=self.saved_image_url)


def _client(repository, *, generator=None, use_route_auth=False):
    app = FastAPI()
    app.include_router(route_mod.router)
    app.dependency_overrides[get_admin_meal_catalog_repository] = lambda: repository
    if not use_route_auth:
        app.dependency_overrides[require_admin_or_local] = lambda: "admin@nutree.ai"
    if generator is not None:
        app.dependency_overrides[get_catalog_image_generator_factory] = lambda: (
            lambda: generator
        )
    return TestClient(app, client=("127.0.0.1", 50000))


def _projection(image_url: str | None = None):
    return AdminCatalogMealProjection(
        meal=CatalogMeal(
            id="catalog-1",
            catalog_key="pho-ga",
            content_hash="a" * 64,
            name="Pho Ga",
            cuisine="vietnamese",
            description="Chicken noodle soup",
            image_url=image_url,
            protein_g=Decimal("38"),
            carbs_g=Decimal("58"),
            fat_g=Decimal("9"),
            fiber_g=Decimal("5"),
            meal_types=("lunch", "dinner"),
            ingredients=(
                CatalogMealIngredient(
                    food_reference_id=7,
                    display_name="Chicken breast",
                    quantity=Decimal("120"),
                    unit="g",
                ),
            ),
        ),
        created_at=datetime(2026, 7, 22, tzinfo=UTC),
        updated_at=datetime(2026, 7, 22, tzinfo=UTC),
    )


def _row(image_url: str | None = None):
    return SimpleNamespace(
        id="catalog-1",
        name="Pho Ga",
        cuisine="vietnamese",
        image_url=image_url,
        ingredients=[SimpleNamespace(display_name="Chicken breast")],
    )


def test_catalog_image_generator_uses_cloudflare_images_store(monkeypatch):
    from src.api import base_dependencies as deps
    from src.infra.adapters.cloudflare_images_store import CloudflareImagesStore

    monkeypatch.setattr(deps.settings, "CLOUDFLARE_ACCOUNT_ID", "account-1")
    monkeypatch.setattr(deps.settings, "CLOUDFLARE_API_TOKEN", "token-1")
    monkeypatch.setattr(deps.settings, "CLOUDFLARE_IMAGES_API_TOKEN", "")
    monkeypatch.setattr(deps.settings, "CLOUDFLARE_IMAGES_VARIANT", "public")
    monkeypatch.setattr(deps.settings, "CLOUDFLARE_WORKERS_AI_IMAGE_MODEL", "@cf/test")
    monkeypatch.setattr(deps.settings, "CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS", 30)

    generator = deps.get_catalog_image_generator()

    assert isinstance(generator._image_store, CloudflareImagesStore)
    assert generator._image_store._account_id == "account-1"
    assert generator._image_store._api_token == "token-1"
