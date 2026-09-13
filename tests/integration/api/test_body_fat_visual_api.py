"""Authenticated API tests for persisted visual body-fat selections."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from src.api.dependencies.auth import get_current_user_id
from src.api.main import app
from src.infra.database.models.user.body_fat_visual_profile import BodyFatVisualProfile
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.user import User
from src.infra.repositories.body_fat_visual_profile_repository_async import (
    AsyncBodyFatVisualProfileRepository,
)


class _AsyncSessionAdapter:
    def __init__(self, session):
        self._session = session

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement):
        return self._session.execute(statement)


class _SQLiteAsyncUnitOfWork:
    def __init__(self, session):
        self.session = _AsyncSessionAdapter(session)
        self._test_session = session
        self.body_fat_visual_profiles = AsyncBodyFatVisualProfileRepository(
            self.session
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type:
            self._test_session.rollback()
        else:
            self._test_session.commit()


def valid_payload(**overrides):
    payload = {
        "schema_version": 1,
        "range_catalog_version": 1,
        "sex_at_selection": "male",
        "start_range_id": None,
        "current_range_id": "male_17_20",
        "target_range_id": "male_13_16",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def two_authenticated_users(test_session) -> tuple[User, User]:
    users = []
    for suffix, body_fat_percentage in (("one", 18.5), ("two", 22.0)):
        user = User(
            id=f"body-fat-visual-user-{suffix}",
            firebase_uid=f"body-fat-visual-firebase-{suffix}",
            email=f"body-fat-visual-{suffix}@example.com",
            username=f"body-fat-visual-{suffix}",
            password_hash="test-hash",
            is_active=True,
        )
        test_session.add(user)
        test_session.add(
            UserProfile(
                user_id=user.id,
                age=30,
                gender="male",
                height_cm=175,
                weight_kg=70,
                body_fat_percentage=body_fat_percentage,
                job_type="desk",
                training_days_per_week=4,
                training_minutes_per_session=60,
                fitness_goal="recomp",
                is_current=True,
            )
        )
        users.append(user)
    test_session.commit()
    return tuple(users)


@pytest.fixture
def real_handler_client(
    api_client, test_session, two_authenticated_users, monkeypatch
) -> Generator[tuple[TestClient, dict[str, str]]]:
    import src.api.base_dependencies as base_dependencies_module
    import src.app.handlers.query_handlers.get_user_tdee_query_handler as tdee_handler_module
    import src.infra.database.uow_async as uow_module

    user_one, user_two = two_authenticated_users
    active_user = {"id": user_one.id}

    def sqlite_uow():
        return _SQLiteAsyncUnitOfWork(test_session)

    monkeypatch.setattr(uow_module, "AsyncUnitOfWork", sqlite_uow)
    monkeypatch.setattr(tdee_handler_module, "AsyncUnitOfWork", sqlite_uow)
    monkeypatch.setattr(
        base_dependencies_module, "get_fat_secret_service_instance", lambda: object()
    )
    app.dependency_overrides[get_current_user_id] = lambda: active_user["id"]
    yield api_client, {"one": user_one.id, "two": user_two.id, "active": active_user}


@pytest.mark.integration
@pytest.mark.api
class TestBodyFatVisualApi:
    def test_get_returns_404_when_visual_selection_is_unset(self, real_handler_client):
        client, _ = real_handler_client

        response = client.get("/v1/user-profiles/body-fat-visual")

        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "error_code": "BODY_FAT_VISUAL_UNSET",
                "message": "No visual body-fat selection has been saved",
                "details": {},
            }
        }

    def test_put_persists_nullable_target_without_changing_measured_value_or_tdee(
        self, real_handler_client, test_session
    ):
        client, users = real_handler_client

        tdee_before = client.get("/v1/user-profiles/tdee")
        response = client.put(
            "/v1/user-profiles/body-fat-visual",
            json=valid_payload(target_range_id=None),
        )
        tdee_after = client.get("/v1/user-profiles/tdee")

        assert tdee_before.status_code == tdee_after.status_code == 200
        assert tdee_after.json() == tdee_before.json()
        assert response.status_code == 200
        assert response.json()["target_range_id"] is None
        assert response.json()["history"][0]["target_range_id"] is None
        profile = test_session.scalar(
            select(UserProfile).where(UserProfile.user_id == users["one"])
        )
        assert profile.body_fat_percentage == 18.5
        rows = test_session.scalars(
            select(BodyFatVisualProfile).where(
                BodyFatVisualProfile.user_id == users["one"]
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].target_range_id is None

    def test_put_appends_ordered_database_history_and_isolates_authenticated_users(
        self, real_handler_client, test_session
    ):
        client, users = real_handler_client
        client.put("/v1/user-profiles/body-fat-visual", json=valid_payload())
        second = client.put(
            "/v1/user-profiles/body-fat-visual",
            json=valid_payload(
                current_range_id="male_21_24", target_range_id="male_17_20"
            ),
        )

        assert second.status_code == 200
        assert [entry["current_range_id"] for entry in second.json()["history"]] == [
            "male_17_20",
            "male_21_24",
        ]
        first_user_rows = test_session.scalars(
            select(BodyFatVisualProfile)
            .where(BodyFatVisualProfile.user_id == users["one"])
            .order_by(BodyFatVisualProfile.updated_at, BodyFatVisualProfile.id)
        ).all()
        assert [row.current_range_id for row in first_user_rows] == [
            "male_17_20",
            "male_21_24",
        ]
        assert len(first_user_rows) == 2
        assert first_user_rows[0].updated_at <= first_user_rows[1].updated_at

        users["active"]["id"] = users["two"]
        assert client.get("/v1/user-profiles/body-fat-visual").status_code == 404
        assert (
            client.put(
                "/v1/user-profiles/body-fat-visual",
                json=valid_payload(current_range_id="male_25_29"),
            ).status_code
            == 200
        )

        users["active"]["id"] = users["one"]
        first_user_response = client.get("/v1/user-profiles/body-fat-visual")
        assert [
            entry["current_range_id"] for entry in first_user_response.json()["history"]
        ] == [
            "male_17_20",
            "male_21_24",
        ]
        assert (
            test_session.scalar(
                select(func.count())
                .where(BodyFatVisualProfile.user_id == users["two"])
                .select_from(BodyFatVisualProfile)
            )
            == 1
        )

    def test_put_persists_start_range_on_latest_selection(
        self, real_handler_client, test_session
    ):
        client, users = real_handler_client

        response = client.put(
            "/v1/user-profiles/body-fat-visual",
            json=valid_payload(
                start_range_id="male_30_plus",
                current_range_id="male_21_24",
                target_range_id="male_13_16",
            ),
        )

        assert response.status_code == 200
        assert response.json()["start_range_id"] == "male_30_plus"
        assert response.json()["history"][0]["start_range_id"] == "male_30_plus"
        row = test_session.scalar(
            select(BodyFatVisualProfile).where(
                BodyFatVisualProfile.user_id == users["one"]
            )
        )
        assert row.start_range_id == "male_30_plus"

    @pytest.mark.parametrize(
        "payload",
        [
            valid_payload(current_range_id="male_not_in_catalog"),
            valid_payload(start_range_id="female_22_25"),
            valid_payload(current_range_id="female_22_25"),
        ],
        ids=["unknown-range-id", "cross-sex-start-range-id", "cross-sex-range-id"],
    )
    def test_put_rejects_invalid_or_cross_sex_range_ids(
        self, real_handler_client, payload
    ):
        client, _ = real_handler_client

        response = client.put("/v1/user-profiles/body-fat-visual", json=payload)

        assert response.status_code == 422
