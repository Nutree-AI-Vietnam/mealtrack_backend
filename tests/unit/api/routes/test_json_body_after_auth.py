"""Routes taking large JSON bodies refuse a caller before reading the body."""

import json
from unittest.mock import AsyncMock

import pytest
from firebase_admin import auth as firebase_auth

from src.api.base_dependencies import get_async_db
from src.api.dependencies import auth
from src.api.main import app
from src.api.routes.v1 import admin_meal_catalog_import as catalog_routes
from src.app.services.catalog_meal_seed_import_service import CatalogSeedImportSummary

MANIFEST_PATHS = [
    "/v1/admin/meal-catalog/import",
    "/v1/admin/meal-catalog/resolve",
    "/v1/admin/meal-catalog/enrich",
]
RECOGNIZE_PATH = "/v1/ingredients/recognize"
ADMIN_EMAIL = "admin@nutree.ai"
TOKENS = {
    "admin-token": {"uid": "admin-uid", "email": ADMIN_EMAIL},
    "user-token": {"uid": "user-uid", "email": "user@example.com"},
}
MANIFEST_BODY = {
    "manifest": {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [
            {
                "recipe_key": "pho-ga-001",
                "cuisine": "vietnamese",
                "name": "Pho Ga",
                "meal_types": ["lunch"],
                "ingredients": [
                    {
                        "food_reference_id": 1,
                        "name": "Chicken breast",
                        "quantity": 120,
                        "unit": "g",
                    }
                ],
            }
        ],
    },
    "partial": True,
    "min_per_cuisine_meal_type": 0,
}


@pytest.fixture(autouse=True)
def firebase(monkeypatch):
    """Firebase accepts only TOKENS; ADMIN_EMAIL is the one admin."""

    async def verify(token: str, *, check_revoked: bool = False) -> dict:
        if token not in TOKENS:
            raise firebase_auth.InvalidIdTokenError("unknown test token")
        return TOKENS[token]

    monkeypatch.setattr(auth, "_verify_id_token", verify)
    monkeypatch.setattr(auth.settings, "ADMIN_EMAILS", ADMIN_EMAIL)


class _Post:
    """One JSON POST; counts the body reads the app makes."""

    def __init__(self, body: object) -> None:
        self.body = json.dumps(body).encode()
        self.reads = 0
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        self.reads += 1
        if self.reads == 1:
            return {"type": "http.request", "body": self.body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int:
        return self.sent[0]["status"]

    def json(self) -> object:
        return json.loads(b"".join(m.get("body", b"") for m in self.sent[1:]))


async def _post(path: str, body: object, token: str | None) -> _Post:
    """Send the POST through the whole app, middleware included."""
    post = _Post(body)
    headers = [
        (b"host", b"testserver"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(post.body)).encode()),
    ]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        # Not loopback, so local development's admin access does not apply.
        "client": ("203.0.113.7", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, post.receive, post.send)
    return post


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "token", "status"),
    [
        *[(path, None, 401) for path in MANIFEST_PATHS],
        *[(path, "expired-token", 401) for path in MANIFEST_PATHS],
        *[(path, "user-token", 403) for path in MANIFEST_PATHS],
        (RECOGNIZE_PATH, None, 401),
        (RECOGNIZE_PATH, "expired-token", 401),
    ],
)
async def test_refused_caller_is_answered_without_reading_the_body(path, token, status):
    post = await _post(path, MANIFEST_BODY, token)

    assert post.status == status
    assert post.reads == 0


@pytest.fixture
def catalog(monkeypatch):
    """Resolve with the database and importer stubbed out."""

    async def run_importer(importer, request, *, dry_run):
        return CatalogSeedImportSummary(dry_run=dry_run, inserted=1)

    monkeypatch.setattr(catalog_routes, "_run_importer", run_importer)
    app.dependency_overrides[get_async_db] = lambda: AsyncMock()
    app.dependency_overrides[catalog_routes.get_catalog_meal_seed_importer] = lambda: (
        object()
    )
    yield
    app.dependency_overrides.pop(get_async_db, None)
    app.dependency_overrides.pop(catalog_routes.get_catalog_meal_seed_importer, None)


@pytest.mark.asyncio
async def test_admin_manifest_is_read_and_resolved(catalog):
    post = await _post("/v1/admin/meal-catalog/resolve", MANIFEST_BODY, "admin-token")

    assert post.status == 200
    assert post.json()["recipe_count"] == 1
    assert post.reads == 1


@pytest.mark.asyncio
async def test_admin_manifest_of_the_wrong_shape_gets_422(catalog):
    post = await _post(
        "/v1/admin/meal-catalog/resolve", {"manifest": []}, "admin-token"
    )

    assert post.status == 422
    [error] = post.json()["detail"]
    assert (error["type"], error["loc"]) == ("dict_type", ["body", "manifest"])
