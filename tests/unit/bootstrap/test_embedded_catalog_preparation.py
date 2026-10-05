import asyncio

import pytest

from src.bootstrap import catalog_preparation
from src.bootstrap.embedded_catalog_preparation import (
    start_embedded_catalog_preparation,
)
from src.infra.workers import catalog_preparation_runtime, catalog_preparation_worker


class _Engine:
    disposed = False

    async def dispose(self):
        self.disposed = True


class _Worker:
    instances: list["_Worker"] = []

    def __init__(self, session_factory, computer, **kwargs):
        self.kwargs = kwargs
        self.started = asyncio.Event()
        self.stopped = False
        _Worker.instances.append(self)

    async def run(self, stop):
        self.started.set()
        await stop.wait()
        self.stopped = True


@pytest.fixture
def fakes(monkeypatch):
    engine = _Engine()
    _Worker.instances.clear()
    monkeypatch.setattr(
        catalog_preparation_runtime, "create_worker_engine", lambda capacity: engine
    )
    monkeypatch.setattr(
        catalog_preparation, "build_preparation_computer", lambda http_client: object()
    )
    monkeypatch.setattr(catalog_preparation_worker, "CatalogPreparationWorker", _Worker)
    return engine


@pytest.mark.asyncio
async def test_disabled_flag_does_not_start_worker(monkeypatch, fakes):
    monkeypatch.setenv("CATALOG_PREPARATION_IN_PROCESS_ENABLED", "false")

    assert await start_embedded_catalog_preparation() is None
    assert _Worker.instances == []


@pytest.mark.asyncio
async def test_worker_runs_until_stopped_then_releases_resources(monkeypatch, fakes):
    monkeypatch.delenv("CATALOG_PREPARATION_IN_PROCESS_ENABLED", raising=False)
    monkeypatch.setenv("CATALOG_PREPARATION_LOCALES", "vi, en")

    handle = await start_embedded_catalog_preparation()
    worker = _Worker.instances[0]
    await asyncio.wait_for(worker.started.wait(), 1)

    assert worker.kwargs["concurrency"] == 1
    assert worker.kwargs["locales"] == ("vi", "en")
    await handle.stop()
    assert worker.stopped
    assert handle.task.done()
    assert fakes.disposed
    assert handle.client.is_closed
