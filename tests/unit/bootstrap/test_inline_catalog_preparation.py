import pytest

from src.bootstrap import catalog_preparation
from src.bootstrap.inline_catalog_preparation import (
    preparation_locales,
    prepare_pending_catalog,
)
from src.infra.config.settings import settings
from src.infra.workers import catalog_preparation_runtime, catalog_preparation_worker


class _Engine:
    disposed = False

    async def dispose(self):
        self.disposed = True


def _install(monkeypatch, outcomes):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    engine = _Engine()
    created = []

    class _Worker:
        def __init__(self, session_factory, computer, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        async def run_once(self):
            outcome = outcomes.pop(0) if outcomes else False
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(
        catalog_preparation_runtime, "create_worker_engine", lambda capacity: engine
    )
    monkeypatch.setattr(
        catalog_preparation, "build_preparation_computer", lambda http_client: object()
    )
    monkeypatch.setattr(catalog_preparation_worker, "CatalogPreparationWorker", _Worker)
    return engine, created


@pytest.mark.asyncio
async def test_runs_jobs_until_none_are_claimable(monkeypatch):
    monkeypatch.setenv("CATALOG_PREPARATION_LOCALES", "vi, en")
    engine, created = _install(monkeypatch, [True, True, True, False])

    processed = await prepare_pending_catalog(concurrency=1)

    assert processed == 3
    assert created[0].kwargs["locales"] == ("vi", "en")
    assert engine.disposed


@pytest.mark.asyncio
async def test_job_error_stops_the_lane_and_releases_the_engine(monkeypatch):
    engine, _ = _install(monkeypatch, [True, RuntimeError("db down"), True])

    processed = await prepare_pending_catalog(concurrency=1)

    assert processed == 1
    assert engine.disposed


@pytest.mark.asyncio
async def test_max_jobs_bounds_the_run(monkeypatch):
    _install(monkeypatch, [True] * 10)

    assert await prepare_pending_catalog(concurrency=1, max_jobs=4) == 4


@pytest.mark.asyncio
async def test_missing_openai_key_leaves_jobs_pending(monkeypatch):
    engine, created = _install(monkeypatch, [True])
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)

    assert await prepare_pending_catalog() == 0
    assert created == []
    assert not engine.disposed


def test_locales_default_to_vietnamese_and_english(monkeypatch):
    monkeypatch.delenv("CATALOG_PREPARATION_LOCALES", raising=False)

    assert preparation_locales() == ("vi", "en")
