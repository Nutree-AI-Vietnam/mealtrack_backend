"""Memory profiler: off by default, refused in production, stats when enabled."""

import os
import tracemalloc

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import require_admin
from src.api.ops import memprof
from src.api.routes.v1.memprof import router


@pytest.fixture(autouse=True)
def _reset_memprof():
    memprof.reset_state_for_tests()
    yield
    memprof.reset_state_for_tests()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: "admin@example.com"
    return TestClient(app)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEMPROF_ENABLED", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")

    assert memprof.flag_requested() is False
    assert memprof.is_enabled() is False
    assert memprof.startup_decision() == "disabled"
    assert memprof.start_if_enabled() is False

    was_tracing = tracemalloc.is_tracing()
    response = _client().get("/v1/admin/memory/snapshot")
    assert response.status_code == 404
    assert response.json()["detail"] == "memory profiler is disabled"
    assert tracemalloc.is_tracing() is was_tracing


def test_refuses_in_production_even_when_flag_is_set(monkeypatch):
    monkeypatch.setenv("MEMPROF_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")

    assert memprof.is_enabled() is False
    assert memprof.startup_decision() == "refuse_production"
    assert memprof.start_if_enabled() is False
    with pytest.raises(memprof.MemprofError) as exc_info:
        memprof.take_report()
    assert exc_info.value.code == "production"

    was_tracing = tracemalloc.is_tracing()
    response = _client().get("/v1/admin/memory/snapshot")
    assert response.status_code == 403
    assert response.json()["detail"] == "memory profiler is refused in production"
    assert tracemalloc.is_tracing() is was_tracing


def test_blank_environment_stays_off(monkeypatch):
    monkeypatch.setenv("MEMPROF_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "  ")
    assert memprof.startup_decision() == "refuse_unset_environment"
    assert memprof.start_if_enabled() is False


def test_returns_stats_and_diff_when_enabled(monkeypatch):
    monkeypatch.setenv("MEMPROF_ENABLED", "TRUE")
    monkeypatch.setenv("ENVIRONMENT", "staging")

    assert memprof.start_if_enabled() is True
    assert tracemalloc.is_tracing() is True
    assert tracemalloc.get_traceback_limit() == memprof.TRACE_FRAMES

    client = _client()
    held: list[bytearray] = []
    first = client.get("/v1/admin/memory/snapshot")
    held.append(bytearray(400_000))
    second = client.get("/v1/admin/memory/snapshot")

    assert first.status_code == 200
    assert second.status_code == 200
    before = first.json()
    after = second.json()

    assert before["pid"] == os.getpid()
    assert after["pid"] == os.getpid()
    assert before["enabled"] is True
    assert before["nframe"] == 25
    assert before["group_by"] == "traceback"
    assert before["diff"]["has_previous"] is False
    assert before["diff"]["top"] == []
    assert 1 <= len(before["top"]) <= 30
    assert {"rank", "size_bytes", "count", "traceback"} <= set(before["top"][0])

    assert after["diff"]["has_previous"] is True
    assert after["diff"]["previous_snapshot_at"] == before["snapshot_at"]
    assert 1 <= len(after["diff"]["top"]) <= 30
    assert any(row["size_diff_bytes"] > 0 for row in after["diff"]["top"])
    assert any(
        "test_memprof.py" in frame
        for row in after["diff"]["top"]
        for frame in row["traceback"]
    )
    del held


def test_snapshot_requires_authentication():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get("/v1/admin/memory/snapshot")
    assert response.status_code == 401


def test_app_registers_admin_snapshot_route():
    from src.api.main import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v1/admin/memory/snapshot" in paths
