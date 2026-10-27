import gc
import json
from itertools import accumulate
from tempfile import SpooledTemporaryFile

import pytest
from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.params import Form
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette import formparsers

from src.api.main import app as mealtrack_app
from src.api.middleware.body_limit import (
    BODY_LIMITS,
    CATALOG_MANIFEST_BODY_MAX_BYTES,
    DEFAULT_BODY_MAX_BYTES,
    INGREDIENT_PHOTO_BODY_MAX_BYTES,
    MEAL_PHOTO_BODY_MAX_BYTES,
    MIB,
    PREVIEW_BODY_MAX_BYTES,
    BodyLimitMiddleware,
)
from src.api.schemas.response.admin_meal_catalog_responses import (
    AdminMealCatalogImportRequest,
)

PREVIEW_PATH = "/v1/tdee/preview"
ANALYZE_PATH = "/v1/meals/image/analyze"
DIRECT_UPLOAD_PATH = "/v1/meals/direct-upload/0b6f3c2e-6d1a-4a8e-9c47-2f5d8e1a7b90"
RESOLVE_PATH = "/v1/admin/meal-catalog/resolve"
LIMITED_PATHS = [
    (PREVIEW_PATH, PREVIEW_BODY_MAX_BYTES),
    (ANALYZE_PATH, MEAL_PHOTO_BODY_MAX_BYTES),
    (DIRECT_UPLOAD_PATH, MEAL_PHOTO_BODY_MAX_BYTES),
    ("/v1/ingredients/recognize", INGREDIENT_PHOTO_BODY_MAX_BYTES),
    ("/v1/admin/meal-catalog/import", CATALOG_MANIFEST_BODY_MAX_BYTES),
    (RESOLVE_PATH, CATALOG_MANIFEST_BODY_MAX_BYTES),
    ("/v1/admin/meal-catalog/enrich", CATALOG_MANIFEST_BODY_MAX_BYTES),
]
# Not in BODY_LIMITS: a JSON route, and a path that only starts like a listed one.
UNLISTED_PATHS = [
    ("/v1/meals/manual", DEFAULT_BODY_MAX_BYTES),
    (f"{DIRECT_UPLOAD_PATH}/extra", DEFAULT_BODY_MAX_BYTES),
]
TOO_LARGE = {"detail": {"code": "request_too_large"}}
BOUNDARY = "body-limit-test"
MULTIPART = (b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode())


class _Client:
    """Sends a body in chunks, counts the reads and records the response."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._messages = [
            {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
            for i, chunk in enumerate(chunks)
        ]
        self.reads = 0
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        self.reads += 1
        if self._messages:
            return self._messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    @property
    def statuses(self) -> list[int]:
        return [m["status"] for m in self.sent if m["type"] == "http.response.start"]

    def json(self) -> object:
        return json.loads(b"".join(m.get("body", b"") for m in self.sent[1:]))


def _scope(path: str, *headers: tuple[bytes, bytes]) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


def _photo_chunks(photo_size: int) -> list[bytes]:
    """One multipart photo part, streamed in 1 MiB chunks."""
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="meal.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode()
    full_chunks, rest = divmod(photo_size, MIB)
    photo = [b"x" * MIB] * full_chunks + ([b"x" * rest] if rest else [])
    return [head, *photo, f"\r\n--{BOUNDARY}--\r\n".encode()]


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            return body


def _catalog_manifest(recipe_count: int) -> dict:
    """Shaped like convert_excel_recipes_to_manifest.py output, with 12
    ingredients and 5 steps of about 450 characters per recipe."""
    step = (
        "Cá mối làm sạch, ướp nước mắm, đường, tiêu và hành tỏi băm rồi kho lửa nhỏ. "
        * 6
    )
    recipes = [
        {
            "recipe_key": f"ca-moi-kho-tieu-{index}",
            "name": "Cá mối kho tiêu",
            "cuisine": "vietnamese",
            "meal_types": ["lunch", "dinner"],
            "popularity_rank": index,
            "equipment": "Nồi đất",
            "allergens": "cá",
            "prep_time_minutes": 15,
            "cook_time_minutes": 45,
            "ingredients": [
                {
                    "name": f"Nguyên liệu {n}",
                    "quantity": 100,
                    "unit": "g",
                    "category": "pantry",
                }
                for n in range(1, 13)
            ],
            "steps": [
                {"step_number": n, "title": "Sơ chế", "description": step}
                for n in range(1, 6)
            ],
            "nutrition": {
                "calories": 850,
                "protein": 91.0,
                "carbs": 66.0,
                "fat": 25.0,
                "fiber": 1.5,
                "sugar": 50.0,
            },
        }
        for index in range(1, recipe_count + 1)
    ]
    return {
        "release_key": "excel-import-recipes",
        "expected_recipe_count": recipe_count,
        "recipes": recipes,
    }


@pytest.fixture
def upload_app() -> tuple[FastAPI, dict[str, int]]:
    """A photo route like the real one: multipart file, auth dependency, handler."""
    calls = {"auth": 0, "handler": 0}
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware)

    def current_user_id() -> str:
        calls["auth"] += 1
        return "user-1"

    @app.post(ANALYZE_PATH)
    async def analyze(
        file: UploadFile = File(...), user_id: str = Depends(current_user_id)
    ) -> dict[str, int]:
        calls["handler"] += 1
        return {"size": len(await file.read())}

    return app, calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "limit"), LIMITED_PATHS + UNLISTED_PATHS)
async def test_declared_length_over_the_limit_is_refused_unread(path, limit):
    client = _Client([b""])

    async def app(scope, receive, send) -> None:
        raise AssertionError("oversized requests must not reach FastAPI")

    oversize = (b"content-length", str(limit + 1).encode())
    await BodyLimitMiddleware(app)(_scope(path, oversize), client.receive, client.send)

    assert client.reads == 0
    assert client.statuses == [413]
    assert client.json() == TOO_LARGE


@pytest.mark.asyncio
async def test_chunked_upload_over_the_limit_stops_before_auth_or_handler(upload_app):
    app, calls = upload_app
    chunks = _photo_chunks(MEAL_PHOTO_BODY_MAX_BYTES + 4 * MIB)
    client = _Client(chunks)

    chunked = (b"transfer-encoding", b"chunked")
    await app(_scope(ANALYZE_PATH, MULTIPART, chunked), client.receive, client.send)

    assert client.statuses == [413]
    assert client.json() == TOO_LARGE
    assert calls == {"auth": 0, "handler": 0}
    # Reading stopped at the first chunk past the limit.
    sizes = accumulate(len(chunk) for chunk in chunks)
    first_over = next(
        n for n, total in enumerate(sizes, 1) if total > MEAL_PHOTO_BODY_MAX_BYTES
    )
    assert client.reads == first_over < len(chunks)


@pytest.mark.asyncio
async def test_chunked_upload_over_the_limit_closes_what_it_spooled(
    upload_app, monkeypatch
):
    app, _ = upload_app
    spooled: list[SpooledTemporaryFile] = []

    class RecordedSpool(SpooledTemporaryFile):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            spooled.append(self)

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", RecordedSpool)
    client = _Client(_photo_chunks(MEAL_PHOTO_BODY_MAX_BYTES + MIB))

    gc.disable()  # the parser must close them, not a garbage collection
    try:
        await app(_scope(ANALYZE_PATH, MULTIPART), client.receive, client.send)
    finally:
        gc.enable()

    assert client.statuses == [413]
    assert spooled
    assert all(file.closed for file in spooled)


def test_full_size_photo_reaches_the_handler(upload_app):
    app, calls = upload_app
    photo = b"x" * (10 * MIB)  # the photo routes' own file cap

    response = TestClient(app).post(
        ANALYZE_PATH, files={"file": ("meal.jpg", photo, "image/jpeg")}
    )

    assert response.status_code == 200
    assert response.json() == {"size": len(photo)}
    assert calls == {"auth": 1, "handler": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "limit"), LIMITED_PATHS + UNLISTED_PATHS)
async def test_body_at_the_limit_reaches_the_app_intact(path, limit):
    body = b"a" * limit
    client = _Client([body[: limit // 2], body[limit // 2 :]])
    received: list[bytes] = []

    async def app(scope, receive, send) -> None:
        received.append(await _read_body(receive))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    at_limit = (b"content-length", str(limit).encode())
    await BodyLimitMiddleware(app)(_scope(path, at_limit), client.receive, client.send)

    assert received == [body]
    assert client.statuses == [204]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "limit"), LIMITED_PATHS + UNLISTED_PATHS)
async def test_streamed_body_one_byte_over_gets_413_not_the_apps_error(path, limit):
    client = _Client([b"a" * limit, b"b"])
    received: list[bytes] = []

    async def app(scope, receive, send) -> None:
        try:
            received.append(await _read_body(receive))
        except Exception:
            # FastAPI answers a failed body read with 400.
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"bad body"})

    await BodyLimitMiddleware(app)(_scope(path), client.receive, client.send)

    assert received == []
    assert client.statuses == [413]
    assert client.json() == TOO_LARGE


async def _app_letting_the_read_error_escape(scope, receive, send) -> None:
    await _read_body(receive)


async def _app_swallowing_the_read_error(scope, receive, send) -> None:
    try:
        await _read_body(receive)
    except Exception:
        return


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "app", [_app_letting_the_read_error_escape, _app_swallowing_the_read_error]
)
async def test_read_error_the_app_does_not_answer_still_gets_413(app):
    client = _Client([b"a" * PREVIEW_BODY_MAX_BYTES, b"b"])

    await BodyLimitMiddleware(app)(_scope(PREVIEW_PATH), client.receive, client.send)

    assert client.statuses == [413]
    assert client.json() == TOO_LARGE


@pytest.mark.asyncio
async def test_read_error_after_the_app_started_responding_propagates():
    client = _Client([b"a" * PREVIEW_BODY_MAX_BYTES, b"b"])

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await _read_body(receive)

    # An OSError, which is also what makes Starlette close spooled files.
    with pytest.raises(OSError):
        await BodyLimitMiddleware(app)(
            _scope(PREVIEW_PATH), client.receive, client.send
        )

    assert client.statuses == [200]


@pytest.mark.asyncio
@pytest.mark.parametrize(("expect", "closes"), [(b"100-continue", True), (None, False)])
async def test_early_413_closes_the_connection_only_for_a_withheld_body(expect, closes):
    headers = [(b"content-length", str(MEAL_PHOTO_BODY_MAX_BYTES + 1).encode())]
    if expect:
        headers.append((b"expect", expect))
    client = _Client([b""])

    async def app(scope, receive, send) -> None:
        raise AssertionError("oversized requests must not reach FastAPI")

    await BodyLimitMiddleware(app)(
        _scope(ANALYZE_PATH, *headers), client.receive, client.send
    )

    assert client.statuses == [413]
    assert ((b"connection", b"close") in client.sent[0]["headers"]) is closes


@pytest.mark.asyncio
async def test_limits_apply_under_a_root_path():
    oversize = (b"content-length", str(MEAL_PHOTO_BODY_MAX_BYTES + 1).encode())
    scope = _scope(f"/api{ANALYZE_PATH}", oversize) | {"root_path": "/api"}
    client = _Client([b""])

    async def app(scope, receive, send) -> None:
        raise AssertionError("oversized requests must not reach FastAPI")

    await BodyLimitMiddleware(app)(scope, client.receive, client.send)

    assert client.statuses == [413]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "limit"), LIMITED_PATHS)
async def test_listed_paths_keep_their_own_limit_instead_of_the_default(path, limit):
    # One byte past the default: within the larger limits, past the preview's.
    client = _Client([b"a" * DEFAULT_BODY_MAX_BYTES, b"b"])

    async def app(scope, receive, send) -> None:
        await _read_body(receive)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await BodyLimitMiddleware(app)(_scope(path), client.receive, client.send)

    assert client.statuses == ([204] if limit > DEFAULT_BODY_MAX_BYTES else [413])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        {"type": "lifespan", "asgi": {"version": "3.0"}},
        _scope(PREVIEW_PATH, (b"content-length", str(100 * MIB).encode()))
        | {"type": "websocket"},
    ],
    ids=["lifespan", "websocket"],
)
async def test_non_http_scopes_pass_through(scope):
    client = _Client([])
    passed: list[object] = []

    async def app(scope, receive, send) -> None:
        passed.append(receive)

    await BodyLimitMiddleware(app)(scope, client.receive, client.send)

    assert passed == [client.receive]
    assert client.sent == []


def test_realistic_catalog_manifest_reaches_the_handler():
    calls = {"auth": 0, "handler": 0}
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware)

    def current_admin() -> str:
        calls["auth"] += 1
        return "admin-1"

    @app.post(RESOLVE_PATH)
    async def resolve(
        request: AdminMealCatalogImportRequest, admin: str = Depends(current_admin)
    ) -> dict[str, int]:
        calls["handler"] += 1
        return {"recipes": len(request.manifest["recipes"])}

    # The 720-recipe Excel import, indented and with escaped non-ASCII text, the
    # largest way a client is likely to send it.
    body = {"manifest": _catalog_manifest(720), "partial": True}
    content = json.dumps(body, indent=2).encode()
    assert DEFAULT_BODY_MAX_BYTES < len(content) <= CATALOG_MANIFEST_BODY_MAX_BYTES

    response = TestClient(app).post(
        RESOLVE_PATH, content=content, headers={"content-type": "application/json"}
    )

    assert response.status_code == 200
    assert response.json() == {"recipes": 720}
    assert calls == {"auth": 1, "handler": 1}


def test_app_limits_every_form_route_with_the_innermost_middleware():
    # Innermost, so CORS and request logging still see its 413.
    assert mealtrack_app.user_middleware[-1].cls is BodyLimitMiddleware
    form_paths = {
        route.path
        for route in mealtrack_app.routes
        if isinstance(route, APIRoute)
        and route.body_field is not None
        and isinstance(route.body_field.field_info, Form)
    }
    assert ANALYZE_PATH in form_paths
    assert form_paths <= BODY_LIMITS.keys()


def test_app_gives_every_catalog_manifest_route_the_manifest_limit():
    # Manifest routes read their body themselves, so find them by the body their
    # OpenAPI entry documents, whether inline or as a component reference.
    def takes_manifest(operation: dict) -> bool:
        content = operation.get("requestBody", {}).get("content", {})
        schema = content.get("application/json", {}).get("schema", {})
        name = AdminMealCatalogImportRequest.__name__
        return schema.get("title") == name or schema.get("$ref", "").endswith(
            f"/{name}"
        )

    manifest_paths = {
        path
        for path, operations in mealtrack_app.openapi()["paths"].items()
        if any(takes_manifest(operation) for operation in operations.values())
    }
    assert RESOLVE_PATH in manifest_paths
    assert {BODY_LIMITS.get(path) for path in manifest_paths} == {
        CATALOG_MANIFEST_BODY_MAX_BYTES
    }
