"""HTTP contract of recipe import: extract, save, capabilities, and the job."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from tests.fixtures.fakes.fake_ai_jobs import (
    JOB_ID,
    USER_ID,
    FakeAiJobsUow,
    StubSchemaProbe,
    make_job,
)

from src.api.dependencies.ai_jobs import get_ai_job_service
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.dependencies.recipe_import import get_recipe_import_guard
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.middleware.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
    reset_limiter_counters,
)
from src.api.routes.v1 import ai_jobs as ai_jobs_mod
from src.api.routes.v1 import recipe_imports as recipe_imports_mod
from src.app.commands.recipe_import import (
    ImportRecipeCommand,
    SaveImportedRecipeCommand,
)
from src.app.services.ai_jobs import AiJobCancelSignals, AiJobService, AiJobWakeup
from src.app.services.recipe_import import RecipeImportGuard
from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.ai_jobs import AiJobType, NewAiJob
from src.domain.model.recipe_import import (
    ImportedIngredient,
    ImportedRecipe,
    IngredientNutritionSource,
    RecipeImportSourceType,
    RecipeImportWarning,
    RecipeMacros,
    RecipeNutrition,
    RecipeSignal,
    RecipeSourceInfo,
    RecipeSourcePlatform,
    RecipeStep,
)

IMAGE_URL = "https://res.cloudinary.com/test/image/upload/v1/mealtrack/abc.jpg"
MACROS = RecipeMacros(
    calories=500.0, protein=40.0, carbs=30.0, fat=20.0, fiber=2.0, sugar=3.0
)
DRAFT = ImportedRecipe(
    import_id="imp123456789",
    language="vi",
    name="Cánh gà chiên",
    servings=2,
    ingredients=(
        ImportedIngredient(
            name="cánh gà",
            english_name="chicken wing, raw",
            quantity=500,
            unit="g",
            grams=500,
            macros=MACROS,
            food_reference_id=7,
            nutrition_source=IngredientNutritionSource.FOOD_REFERENCE,
        ),
    ),
    steps=(RecipeStep("Chiên", 12),),
    nutrition=RecipeNutrition(servings=2, total=MACROS, per_serving=MACROS),
    source=RecipeSourceInfo(
        type=RecipeImportSourceType.URL,
        platform=RecipeSourcePlatform.TIKTOK,
        url="https://www.tiktok.com/@chef/video/1",
        signals=(RecipeSignal.CAPTION,),
    ),
    warnings=(RecipeImportWarning.SERVINGS_ESTIMATED,),
    servings_estimated=True,
    serving_description="1 đĩa, 5 cánh",
    makes="10 cánh gà",
)


class FakeBus:
    def __init__(self, result: Any = DRAFT) -> None:
        self.result = result
        self.sent: list[Any] = []

    async def send(self, command: Any) -> Any:
        self.sent.append(command)
        if isinstance(self.result, BaseException):
            raise self.result
        if callable(self.result):
            return await self.result(command)
        return self.result


def _guard(*, limit: int = 30, counter: AsyncMock | None = None) -> RecipeImportGuard:
    return RecipeImportGuard(
        increment_counter=counter or AsyncMock(return_value=1),
        daily_limit=limit,
    )


@pytest.fixture
def make_client():
    apps: list[FastAPI] = []
    reset_limiter_counters()

    def build(
        bus: FakeBus,
        guard: RecipeImportGuard | None = None,
        *,
        jobs: AiJobService | None = None,
    ) -> TestClient:
        app = FastAPI()
        app.add_middleware(AcceptLanguageMiddleware)
        register_exception_handlers(app)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        app.include_router(recipe_imports_mod.router)
        app.include_router(ai_jobs_mod.router)
        app.dependency_overrides[get_current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_configured_event_bus] = lambda: bus
        app.dependency_overrides[get_recipe_import_guard] = lambda: guard or _guard()
        if jobs is not None:
            app.dependency_overrides[get_ai_job_service] = lambda: jobs
        apps.append(app)
        return TestClient(app)

    yield build
    for app in apps:
        app.dependency_overrides.clear()
    reset_limiter_counters()


class TestExtract:
    def test_returns_the_priced_draft_in_the_request_language(self, make_client):
        bus = FakeBus()
        client = make_client(bus)

        response = client.post(
            "/v1/recipe-imports/extract",
            json={"url": "Xem nè https://vt.tiktok.com/ZS1/"},
            headers={"Accept-Language": "vi-VN"},
        )

        assert response.status_code == 200
        (command,) = bus.sent
        assert isinstance(command, ImportRecipeCommand)
        assert (command.user_id, command.language, command.url) == (
            USER_ID,
            "vi",
            "Xem nè https://vt.tiktok.com/ZS1/",
        )
        body = response.json()
        assert body["import_id"] == "imp123456789"
        assert body["nutrition"]["per_serving"]["calories"] == 500.0
        assert body["nutrition"]["complete"] is True
        assert body["ingredients"][0]["nutrition_source"] == "food_reference"
        assert body["steps"] == [
            {"step": 1, "instruction": "Chiên", "duration_minutes": 12}
        ]
        assert body["source"] == {
            "type": "url",
            "platform": "tiktok",
            "url": "https://www.tiktok.com/@chef/video/1",
            "title": None,
            "author": None,
            "site_name": None,
            "signals": ["caption"],
            "linked_recipe_url": None,
        }
        assert body["warnings"] == ["servings_estimated"]
        assert (
            body["servings"],
            body["servings_estimated"],
            body["serving_description"],
            body["makes"],
        ) == (2, True, "1 đĩa, 5 cánh", "10 cánh gà")

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"url": "https://a.com", "text": "two sources"},
            {"image_url": IMAGE_URL},
            {"url": "   "},
        ],
    )
    def test_needs_exactly_one_source(self, make_client, body):
        bus = FakeBus()

        response = make_client(bus).post("/v1/recipe-imports/extract", json=body)

        assert response.status_code == 422
        assert bus.sent == []

    def test_photo_must_come_from_our_upload_host(self, make_client):
        bus = FakeBus()

        response = make_client(bus).post(
            "/v1/recipe-imports/extract",
            json={
                "image_url": "https://evil.example.com/mealtrack/abc.jpg",
                "image_id": "abc",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == "INVALID_IMAGE_URL"
        assert bus.sent == []

    def test_uploaded_photo_is_accepted(self, make_client):
        bus = FakeBus()

        response = make_client(bus).post(
            "/v1/recipe-imports/extract",
            json={"image_url": IMAGE_URL, "image_id": "abc", "note": "trang 3"},
        )

        assert response.status_code == 200
        assert (bus.sent[0].image_url, bus.sent[0].note) == (IMAGE_URL, "trang 3")

    @pytest.mark.parametrize(
        ("failure", "status"),
        [
            (RecipeImportFailure.URL_NOT_ALLOWED, 400),
            (RecipeImportFailure.NOT_A_RECIPE, 422),
            (RecipeImportFailure.VIDEO_UNAVAILABLE, 422),
            (RecipeImportFailure.SOURCE_UNREACHABLE, 503),
        ],
    )
    def test_import_failures_keep_their_codes(self, make_client, failure, status):
        client = make_client(FakeBus(RecipeImportError(failure, "nope")))

        response = client.post(
            "/v1/recipe-imports/extract", json={"url": "https://a.com/x"}
        )

        assert response.status_code == status
        assert response.json() == {
            "detail": {"error_code": failure.value, "message": "nope", "details": {}}
        }

    def test_daily_quota_is_429(self, make_client):
        bus = FakeBus()

        response = make_client(
            bus, _guard(limit=1, counter=AsyncMock(return_value=2))
        ).post("/v1/recipe-imports/extract", json={"url": "https://a.com/x"})

        assert response.status_code == 429
        assert response.json()["detail"]["error_code"] == "RECIPE_IMPORT_DAILY_LIMIT"
        assert bus.sent == []

    def test_slow_imports_time_out(self, make_client, monkeypatch):
        async def slow(command: Any) -> Any:
            await asyncio.sleep(5)

        monkeypatch.setattr(recipe_imports_mod, "IMPORT_DEADLINE_SECONDS", 0.05)

        response = make_client(FakeBus(slow)).post(
            "/v1/recipe-imports/extract", json={"url": "https://a.com/x"}
        )

        assert response.status_code == 503
        assert response.json()["detail"]["error_code"] == "RECIPE_IMPORT_TIMEOUT"

    def test_rate_limited_per_minute(self, make_client):
        client = make_client(FakeBus())

        statuses = [
            client.post(
                "/v1/recipe-imports/extract", json={"url": "https://a.com/x"}
            ).status_code
            for _ in range(11)
        ]

        assert statuses[:10] == [200] * 10
        assert statuses[10] == 429


class TestSave:
    def _body(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "import_id": "imp123456789",
            "meal_type": "lunch",
            "name": "Cánh gà chiên",
            "servings": 2,
            "ingredients": [
                {
                    "name": "cánh gà",
                    "english_name": "chicken wing, raw",
                    "quantity": 500,
                    "unit": "g",
                    "grams": 500,
                }
            ],
            "steps": [{"instruction": "Chiên", "duration_minutes": 12}],
            "source": {
                "type": "url",
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@chef/video/1",
            },
        }
        return {**body, **overrides}

    def test_sends_the_reviewed_draft_and_returns_the_saved_item(self, make_client):
        saved = {
            "id": "row",
            "suggestion_id": "recipe_import_imp123456789",
            "meal_type": "lunch",
        }
        bus = FakeBus(saved)

        response = make_client(bus).post(
            "/v1/recipe-imports/save",
            json=self._body(),
            headers={"Accept-Language": "vi"},
        )

        assert response.status_code == 200
        assert response.json() == saved
        (command,) = bus.sent
        assert isinstance(command, SaveImportedRecipeCommand)
        assert (command.import_id, command.meal_type, command.language) == (
            "imp123456789",
            "lunch",
            "vi",
        )
        assert command.recipe["ingredients"][0]["english_name"] == "chicken wing, raw"
        assert "import_id" not in command.recipe

    def test_keeps_the_imported_servings_next_to_the_users_choice(self, make_client):
        bus = FakeBus({"id": "row"})

        response = make_client(bus).post(
            "/v1/recipe-imports/save",
            json=self._body(
                servings=3,
                original_servings=2,
                servings_estimated=True,
                serving_description="1 đĩa, 5 cánh",
                makes="10 cánh gà",
            ),
        )

        assert response.status_code == 200
        recipe = bus.sent[0].recipe
        assert (
            recipe["servings"],
            recipe["original_servings"],
            recipe["servings_estimated"],
            recipe["serving_description"],
            recipe["makes"],
        ) == (3, 2, True, "1 đĩa, 5 cánh", "10 cánh gà")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"import_id": "bad id!"},
            {"ingredients": []},
            {"meal_type": "brunch"},
            {"servings": 0},
            {"name": ""},
        ],
    )
    def test_rejects_invalid_drafts(self, make_client, overrides):
        bus = FakeBus({})

        response = make_client(bus).post(
            "/v1/recipe-imports/save", json=self._body(**overrides)
        )

        assert response.status_code == 422
        assert bus.sent == []

    def test_saving_does_not_count_against_the_import_quota(self, make_client):
        counter = AsyncMock(return_value=99)

        response = make_client(FakeBus({}), _guard(limit=1, counter=counter)).post(
            "/v1/recipe-imports/save", json=self._body()
        )

        assert response.status_code == 200
        counter.assert_not_awaited()


def test_capabilities(make_client, monkeypatch):
    monkeypatch.setattr(
        recipe_imports_mod,
        "recipe_import_capabilities",
        lambda: {
            "sources": ["url", "text", "image"],
            "video_platforms": ["youtube", "tiktok", "instagram", "facebook"],
            "video_media_analysis": False,
            "daily_limit": 30,
            "timeout_seconds": 120.0,
        },
    )

    response = make_client(FakeBus()).get("/v1/recipe-imports/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "sources": ["url", "text", "image"],
        "video_platforms": ["youtube", "tiktok", "instagram", "facebook"],
        "video_media_analysis": False,
        "daily_limit": 30,
        "timeout_seconds": 120.0,
    }


def test_capabilities_report_the_fixed_limits():
    from src.bootstrap.recipe_import import recipe_import_capabilities

    capabilities = recipe_import_capabilities()

    assert (capabilities["daily_limit"], capabilities["timeout_seconds"]) == (30, 120.0)
    assert isinstance(capabilities["video_media_analysis"], bool)


class TestRecipeImportJob:
    def _jobs(self) -> tuple[AiJobService, FakeAiJobsUow]:
        uow = FakeAiJobsUow()

        def create(job: NewAiJob):
            return (
                make_job(
                    id=job.id,
                    user_id=job.user_id,
                    job_type=job.job_type,
                    language=job.language,
                    request_payload=job.request_payload,
                    request_fingerprint=job.request_fingerprint,
                ),
                True,
            )

        uow.ai_jobs.create.side_effect = create
        service = AiJobService(
            uow_factory=uow,
            schema_probe=StubSchemaProbe(ready=True),
            wakeup=AiJobWakeup(),
            cancel_signals=AiJobCancelSignals(),
            enabled=True,
            chat_background_enabled=True,
            lease_seconds=120,
        )
        return service, uow

    def test_queues_a_recipe_import_and_counts_the_quota(self, make_client):
        service, uow = self._jobs()
        counter = AsyncMock(return_value=1)
        client = make_client(FakeBus(), _guard(counter=counter), jobs=service)

        response = client.post(
            "/v1/ai-jobs",
            json={
                "id": JOB_ID,
                "type": "recipe_import",
                "input": {"url": " https://vt.tiktok.com/ZS1/ "},
            },
            headers={"Accept-Language": "vi"},
        )

        assert response.status_code == 202
        (job,) = uow.ai_jobs.create.await_args.args
        assert job.job_type == AiJobType.RECIPE_IMPORT
        assert job.request_payload == {
            "url": "https://vt.tiktok.com/ZS1/",
            "text": None,
            "image_url": None,
            "note": None,
        }
        counter.assert_awaited_once()

    def test_invalid_input_is_reported_under_input(self, make_client):
        service, uow = self._jobs()

        response = make_client(FakeBus(), jobs=service).post(
            "/v1/ai-jobs", json={"id": JOB_ID, "type": "recipe_import", "input": {}}
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"][:2] == ["body", "input"]
        uow.ai_jobs.create.assert_not_awaited()

    def test_quota_blocks_the_job(self, make_client):
        service, uow = self._jobs()

        response = make_client(
            FakeBus(), _guard(limit=1, counter=AsyncMock(return_value=2)), jobs=service
        ).post(
            "/v1/ai-jobs",
            json={"id": JOB_ID, "type": "recipe_import", "input": {"text": "x" * 40}},
        )

        assert response.status_code == 429
        uow.ai_jobs.create.assert_not_awaited()
