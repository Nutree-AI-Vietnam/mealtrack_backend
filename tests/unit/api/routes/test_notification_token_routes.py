"""Device push-token registration over a real ``PushTokenService``."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fixtures.fakes.fake_ai_jobs import USER_ID, FakeAiJobsUow, StubSchemaProbe

from src.api.dependencies.ai_jobs import get_push_token_service
from src.api.dependencies.auth import get_current_user_id
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import notifications as notifications_mod
from src.app.services.ai_jobs import PushTokenService
from src.domain.model.notification import DeviceType

TOKEN = "fcm-token-1"


@dataclass
class _Api:
    client: TestClient
    uow: FakeAiJobsUow
    probe: StubSchemaProbe


@pytest.fixture
def make_api():
    apps: list[FastAPI] = []

    def build(*, ready: bool = True) -> _Api:
        uow = FakeAiJobsUow()
        probe = StubSchemaProbe(ready=ready)
        service = PushTokenService(uow_factory=uow, schema_probe=probe)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(notifications_mod.router)
        app.dependency_overrides[get_current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_push_token_service] = lambda: service
        apps.append(app)
        return _Api(TestClient(app), uow, probe)

    yield build
    for app in apps:
        app.dependency_overrides.clear()


@pytest.fixture
def api(make_api) -> _Api:
    return make_api()


def _unregister(client: TestClient, body: dict):
    return client.request("DELETE", "/v1/notifications/tokens", json=body)


class TestRegister:
    @pytest.mark.parametrize("device_type", list(DeviceType))
    def test_token_is_stored_for_the_user(self, api, device_type):
        response = api.client.post(
            "/v1/notifications/tokens",
            json={
                "fcm_token": TOKEN,
                "device_type": device_type.value,
                "timezone": "Asia/Ho_Chi_Minh",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Token registered"}
        api.uow.push_tokens.upsert.assert_awaited_once_with(
            user_id=USER_ID, token=TOKEN, device_type=device_type
        )
        assert api.uow.committed == 1

    def test_longest_token_is_accepted(self, api):
        response = api.client.post(
            "/v1/notifications/tokens",
            json={"fcm_token": "t" * 1024, "device_type": "ios"},
        )

        assert response.status_code == 200
        assert api.uow.push_tokens.upsert.await_args.kwargs["token"] == "t" * 1024

    def test_token_is_acknowledged_before_storage_is_migrated(self, make_api):
        api = make_api(ready=False)

        response = api.client.post(
            "/v1/notifications/tokens",
            json={"fcm_token": TOKEN, "device_type": "android"},
        )

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Token not stored"}
        assert api.probe.calls == 1
        assert api.uow.opened == 0

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"fcm_token": TOKEN, "device_type": "web"}, "device_type"),
            ({"fcm_token": TOKEN}, "device_type"),
            ({"fcm_token": "", "device_type": "ios"}, "fcm_token"),
            ({"fcm_token": "t" * 1025, "device_type": "ios"}, "fcm_token"),
            ({"device_type": "ios"}, "fcm_token"),
            (
                {"fcm_token": TOKEN, "device_type": "ios", "timezone": "x" * 65},
                "timezone",
            ),
        ],
    )
    def test_invalid_registration_is_422(self, api, body, field):
        response = api.client.post("/v1/notifications/tokens", json=body)

        assert response.status_code == 422
        assert [error["loc"] for error in response.json()["detail"]] == [
            ["body", field]
        ]
        assert api.probe.calls == 0
        api.uow.push_tokens.upsert.assert_not_awaited()


class TestUnregister:
    def test_token_is_deactivated(self, api):
        api.uow.push_tokens.deactivate.return_value = True

        response = _unregister(api.client, {"fcm_token": TOKEN})

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Token deleted"}
        api.uow.push_tokens.deactivate.assert_awaited_once_with(
            user_id=USER_ID, token=TOKEN
        )
        assert api.uow.committed == 1

    def test_unknown_token_still_reports_deleted(self, api):
        api.uow.push_tokens.deactivate.return_value = False

        response = _unregister(api.client, {"fcm_token": TOKEN})

        assert response.json() == {"success": True, "message": "Token deleted"}

    def test_delete_before_storage_is_migrated_still_succeeds(self, make_api):
        api = make_api(ready=False)

        response = _unregister(api.client, {"fcm_token": TOKEN})

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Token deleted"}
        assert api.uow.opened == 0

    @pytest.mark.parametrize("body", [{"fcm_token": ""}, {"fcm_token": "t" * 1025}, {}])
    def test_invalid_token_is_422(self, api, body):
        response = _unregister(api.client, body)

        assert response.status_code == 422
        assert [error["loc"] for error in response.json()["detail"]] == [
            ["body", "fcm_token"]
        ]
        api.uow.push_tokens.deactivate.assert_not_awaited()
