"""End-to-end integration tests for Recent and Favorite Meals lifecycle."""

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.main import app
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.user import User
from src.infra.repositories.favorite_meal_repository_async import (
    AsyncFavoriteMealRepository,
)
from src.infra.repositories.hydration_repository_async import (
    AsyncHydrationRepository,
)
from src.infra.repositories.meal_repository_async import AsyncMealRepository
from src.infra.repositories.meal_write_operation_repository_async import (
    AsyncMealWriteOperationRepository,
)
from src.infra.repositories.user_repository_async import AsyncUserRepository


class _AsyncSessionAdapter:
    def __init__(self, session):
        self._session = session

    def add(self, instance) -> None:
        self._session.add(instance)

    def add_all(self, instances) -> None:
        self._session.add_all(instances)

    async def execute(self, statement, *args, **kwargs):
        return self._session.execute(statement, *args, **kwargs)

    async def scalars(self, statement, *args, **kwargs):
        return self._session.scalars(statement, *args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        return self._session.scalar(statement, *args, **kwargs)

    async def flush(self) -> None:
        self._session.flush()

    async def delete(self, instance) -> None:
        self._session.delete(instance)

    async def get(self, entity, ident):
        return self._session.get(entity, ident)

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()


class _SQLiteAsyncUnitOfWork(AsyncUnitOfWorkPort):
    def __init__(self, session):
        self._session = session
        self.session = _AsyncSessionAdapter(session)
        self.meals = AsyncMealRepository(self.session)
        self.favorite_meals = AsyncFavoriteMealRepository(self.session)
        self.meal_write_operations = AsyncMealWriteOperationRepository(self.session)
        self.users = AsyncUserRepository(self.session)
        self.hydration_entries = AsyncHydrationRepository(self.session)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not exc_type:
            self._session.flush()

    async def commit(self) -> None:
        self._session.flush()

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj) -> None:
        self._session.refresh(obj)


@pytest.fixture
def e2e_client(
    api_client, test_session, monkeypatch
) -> Generator[tuple[TestClient, User]]:
    # Create test user in DB
    user_id = str(uuid4())
    user = User(
        id=user_id,
        firebase_uid=f"e2e_firebase_{user_id[:8]}",
        email=f"e2e_{user_id[:8]}@example.com",
        username=f"e2e_user_{user_id[:8]}",
        password_hash="test-hash",
        is_active=True,
        onboarding_completed=True,
        timezone="UTC",
    )

    test_session.add(user)
    profile = UserProfile(
        user_id=user_id,
        age=28,
        gender="female",
        height_cm=165.0,
        weight_kg=55.0,
        is_current=True,
    )
    test_session.add(profile)
    test_session.flush()

    def sqlite_uow():
        return _SQLiteAsyncUnitOfWork(test_session)

    from unittest.mock import AsyncMock

    class MockEventPublisher:
        async def publish(self, *args, **kwargs):
            return True

    mock_pub = MockEventPublisher()

    monkeypatch.setattr(
        "src.app.events.meal.meal_events.publish_meal_event",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.app.graphs.meal_analyze.nodes.publish_meal_event",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.app.handlers.command_handlers.repeat_meal_command_handler.publish_meal_event",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler.publish_meal_event",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.api.dependencies.event_bus.get_integration_event_publisher",
        lambda: mock_pub,
    )
    monkeypatch.setattr(
        "src.bootstrap.integration_services.get_integration_event_publisher",
        lambda: mock_pub,
    )

    import src.api.base_dependencies as base_deps_module
    import src.api.dependencies.event_bus as event_bus_module
    import src.app.handlers.query_handlers.get_meal_by_id_query_handler as get_meal_handler_module
    import src.infra.database.uow_async as uow_module

    # The recent/favorite query handlers receive uow_factory via event_bus
    # composition, so patching uow_async + event_bus covers them.
    monkeypatch.setattr(uow_module, "AsyncUnitOfWork", sqlite_uow)
    monkeypatch.setattr(base_deps_module, "AsyncUnitOfWork", sqlite_uow)
    monkeypatch.setattr(event_bus_module, "AsyncUnitOfWork", sqlite_uow)
    monkeypatch.setattr(get_meal_handler_module, "AsyncUnitOfWork", sqlite_uow)

    monkeypatch.setenv("ENABLE_DEV_AUTH_BYPASS", "0")
    event_bus_module._configured_event_bus = None

    # Rate limiting is not under test; the TestClient shares one IP key, so
    # multi-meal scenarios would trip the 10/minute analyze limit.
    from src.api.middleware.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)

    user_id_str = str(user.id)
    app.dependency_overrides[get_current_user_id] = lambda: user_id_str

    yield api_client, user

    event_bus_module._configured_event_bus = None


@pytest.mark.integration
@pytest.mark.api
class TestRecentFavoriteMealsE2E:
    """E2E integration test suite for recent, favorite, and repeat meal flows."""

    def test_full_recent_favorite_repeat_lifecycle(
        self, e2e_client, sample_image_bytes, test_session
    ):
        client, user = e2e_client

        # 1. Create a fresh meal via image analyze
        files = {"file": ("meal.jpg", sample_image_bytes, "image/jpeg")}
        create_resp = client.post("/v1/meals/image/analyze", files=files)
        assert create_resp.status_code == 200
        meal_1 = create_resp.json()
        meal_1_id = meal_1["meal_id"]
        assert meal_1_id is not None
        assert meal_1["status"] == "ready"

        # 2. Check GET /v1/meals/recent -> meal_1 is in recent, is_favorite is False
        recent_resp = client.get("/v1/meals/recent?limit=10")
        assert recent_resp.status_code == 200
        recent_data = recent_resp.json()
        assert recent_data["total"] >= 1
        found = [m for m in recent_data["items"] if m["meal_id"] == meal_1_id]
        assert len(found) == 1
        assert found[0]["is_favorite"] is False
        assert found[0]["favorited_at"] is None

        # 3. Check GET /v1/meals/favorites -> initially empty
        fav_resp = client.get("/v1/meals/favorites?limit=20")
        assert fav_resp.status_code == 200
        assert fav_resp.json()["total"] == 0

        # 4. Favorite meal_1: PUT /v1/meals/{meal_1_id}/favorite
        fav_action = client.put(f"/v1/meals/{meal_1_id}/favorite")
        assert fav_action.status_code == 200
        action_data = fav_action.json()
        assert action_data["meal_id"] == meal_1_id
        assert action_data["is_favorite"] is True
        assert action_data["favorited_at"] is not None

        # 5. Check GET /v1/meals/favorites -> contains meal_1
        fav_resp2 = client.get("/v1/meals/favorites?limit=20")
        assert fav_resp2.status_code == 200
        fav_items = fav_resp2.json()["items"]
        assert len(fav_items) == 1
        assert fav_items[0]["meal_id"] == meal_1_id
        assert fav_items[0]["is_favorite"] is True
        assert fav_items[0]["favorited_at"] is not None

        # 6. Check GET /v1/meals/recent -> revision invalidated, meal_1 is_favorite is now True
        recent_resp2 = client.get("/v1/meals/recent?limit=10")
        assert recent_resp2.status_code == 200
        found2 = [m for m in recent_resp2.json()["items"] if m["meal_id"] == meal_1_id]
        assert len(found2) == 1
        assert found2[0]["is_favorite"] is True

        # 7. Repeat meal_1: POST /v1/meals/{meal_1_id}/repeat with meal_type override
        repeat_resp = client.post(
            f"/v1/meals/{meal_1_id}/repeat",
            json={"meal_type": "dinner"},
            headers={"Idempotency-Key": "repeat-idempotency-key-001"},
        )
        assert repeat_resp.status_code == 201
        meal_2 = repeat_resp.json()
        meal_2_id = meal_2["meal_id"]
        assert meal_2_id != meal_1_id
        assert meal_2["status"] == "ready"
        assert meal_2["meal_type"] == "dinner"
        assert meal_2["dish_name"] == meal_1["dish_name"]
        assert len(meal_2["food_items"]) == len(meal_1["food_items"])
        orig_item_ids = {item["id"] for item in meal_1["food_items"]}
        new_item_ids = {item["id"] for item in meal_2["food_items"]}
        assert orig_item_ids.isdisjoint(new_item_ids)

        # 8. Repeat with same Idempotency-Key -> exact replay
        replay_resp = client.post(
            f"/v1/meals/{meal_1_id}/repeat",
            json={"meal_type": "dinner"},
            headers={"Idempotency-Key": "repeat-idempotency-key-001"},
        )
        assert replay_resp.status_code == 201
        assert replay_resp.json()["meal_id"] == meal_2_id

        # Verify meal_2 exists directly
        get_m2 = client.get(f"/v1/meals/{meal_2_id}")
        assert get_m2.status_code == 200

        # 9. Delete meal_1: DELETE /v1/meals/{meal_1_id} (soft-delete to INACTIVE)
        del_resp = client.delete(f"/v1/meals/{meal_1_id}")
        assert del_resp.status_code == 200

        # 10. GET /v1/meals/{meal_1_id} -> 404 Not Found (filtered out)
        get_single = client.get(f"/v1/meals/{meal_1_id}")
        assert get_single.status_code == 404

        # 11. GET /v1/meals/recent -> meal_1 is removed from recent list
        recent_resp3 = client.get("/v1/meals/recent?limit=10")
        assert recent_resp3.status_code == 200
        recent_ids = [m["meal_id"] for m in recent_resp3.json().get("items", [])]
        assert meal_1_id not in recent_ids
        assert meal_2_id in recent_ids

        # 12. GET /v1/meals/favorites -> meal_1 is gone. DELETE /v1/meals/{id}
        # hard-deletes the meal row today (no soft-delete/INACTIVE path is
        # implemented), so the favorite membership disappears with the meal.
        fav_resp3 = client.get("/v1/meals/favorites?limit=20")
        assert fav_resp3.status_code == 200
        fav_ids = [m["meal_id"] for m in fav_resp3.json()["items"]]
        assert meal_1_id not in fav_ids

        # 13. Repeat the deleted meal_1 -> 404 (meal row no longer exists)
        repeat_deleted = client.post(
            f"/v1/meals/{meal_1_id}/repeat",
            headers={"Idempotency-Key": "repeat-idempotency-key-002"},
        )
        assert repeat_deleted.status_code == 404

        # 14. Unfavorite deleted meal_1 -> idempotent success
        unfav_resp = client.delete(f"/v1/meals/{meal_1_id}/favorite")
        assert unfav_resp.status_code == 200
        assert unfav_resp.json()["is_favorite"] is False

        fav_resp4 = client.get("/v1/meals/favorites?limit=20")
        assert fav_resp4.status_code == 200
        assert meal_1_id not in [m["meal_id"] for m in fav_resp4.json()["items"]]

    def test_recent_meals_deduplication(
        self, e2e_client, sample_image_bytes, test_session
    ):
        client, user = e2e_client

        # Create two identical meals via image analyze (same mock vision result)
        files1 = {"file": ("meal1.jpg", sample_image_bytes, "image/jpeg")}
        resp1 = client.post("/v1/meals/image/analyze", files=files1)
        assert resp1.status_code == 200
        m1_id = resp1.json()["meal_id"]

        files2 = {"file": ("meal2.jpg", sample_image_bytes, "image/jpeg")}
        resp2 = client.post("/v1/meals/image/analyze", files=files2)
        assert resp2.status_code == 200
        m2_id = resp2.json()["meal_id"]

        assert m1_id != m2_id

        # GET /v1/meals/recent -> both have identical content, only latest should appear
        recent_resp = client.get("/v1/meals/recent?limit=10")
        assert recent_resp.status_code == 200
        recent_items = recent_resp.json()["items"]

        m_ids = [m["meal_id"] for m in recent_items]
        assert m2_id in m_ids
        assert m1_id not in m_ids

    def test_recent_meals_rejects_limit_above_ten(
        self, e2e_client, sample_image_bytes, test_session
    ):
        """AC: recent list is capped at 10 distinct meals."""
        client, _user = e2e_client
        resp = client.get("/v1/meals/recent?limit=11")
        assert resp.status_code == 422

    def test_favorites_cap_rejects_21st_without_eviction(
        self, e2e_client, sample_image_bytes, test_session
    ):
        """AC: favorites cap is 20; the 21st is rejected, nothing is evicted."""
        client, _user = e2e_client

        meal_ids = []
        for i in range(21):
            files = {"file": (f"meal{i}.jpg", sample_image_bytes, "image/jpeg")}
            resp = client.post("/v1/meals/image/analyze", files=files)
            assert resp.status_code == 200
            meal_ids.append(resp.json()["meal_id"])

        # Star the first 20 -> all succeed
        for meal_id in meal_ids[:20]:
            resp = client.put(f"/v1/meals/{meal_id}/favorite")
            assert resp.status_code == 200

        # Re-star an existing favorite at the cap -> idempotent success
        restar = client.put(f"/v1/meals/{meal_ids[0]}/favorite")
        assert restar.status_code == 200
        assert restar.json()["is_favorite"] is True

        # The 21st distinct favorite -> rejected with an error, no eviction
        rejected = client.put(f"/v1/meals/{meal_ids[20]}/favorite")
        assert rejected.status_code == 400
        assert "limit" in rejected.json()["detail"]["message"].lower()

        fav_resp = client.get("/v1/meals/favorites?limit=20")
        assert fav_resp.status_code == 200
        fav_ids = {m["meal_id"] for m in fav_resp.json()["items"]}
        assert fav_ids == set(meal_ids[:20])
        assert meal_ids[20] not in fav_ids

        # After unfavoriting one, the previously rejected meal can be starred
        unfav = client.delete(f"/v1/meals/{meal_ids[0]}/favorite")
        assert unfav.status_code == 200
        retry = client.put(f"/v1/meals/{meal_ids[20]}/favorite")
        assert retry.status_code == 200

    def test_repeat_appends_to_slot_without_overwriting(
        self, e2e_client, sample_image_bytes, test_session
    ):
        """AC: repeat/log-from-list appends into the target slot, never overwrites."""
        client, _user = e2e_client

        files = {"file": ("meal.jpg", sample_image_bytes, "image/jpeg")}
        create_resp = client.post("/v1/meals/image/analyze", files=files)
        assert create_resp.status_code == 200
        source = create_resp.json()
        source_id = source["meal_id"]

        # First repeat into the dinner slot
        first = client.post(
            f"/v1/meals/{source_id}/repeat",
            json={"meal_type": "dinner"},
            headers={"Idempotency-Key": "append-key-1"},
        )
        assert first.status_code == 201
        first_meal = first.json()
        assert first_meal["meal_type"] == "dinner"

        # Second repeat into the SAME dinner slot with a new idempotency key
        second = client.post(
            f"/v1/meals/{source_id}/repeat",
            json={"meal_type": "dinner"},
            headers={"Idempotency-Key": "append-key-2"},
        )
        assert second.status_code == 201
        second_meal = second.json()
        assert second_meal["meal_type"] == "dinner"
        assert second_meal["meal_id"] != first_meal["meal_id"]

        # Both dinner entries coexist; the first was not replaced or emptied
        first_check = client.get(f"/v1/meals/{first_meal['meal_id']}")
        assert first_check.status_code == 200
        first_data = first_check.json()
        assert first_data["meal_type"] == "dinner"
        assert len(first_data["food_items"]) == len(source["food_items"])
        assert {i["id"] for i in first_data["food_items"]} == {
            i["id"] for i in first_meal["food_items"]
        }

        # Source meal is also untouched
        source_check = client.get(f"/v1/meals/{source_id}")
        assert source_check.status_code == 200
        assert len(source_check.json()["food_items"]) == len(source["food_items"])
