"""FeatureFlagReader: one query per TTL, defaults on missing rows and errors."""

import pytest

from src.infra.services.feature_flag_reader import FeatureFlagReader

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement):
        self.owner.queries += 1
        if self.owner.error is not None:
            raise self.owner.error
        return _Result(list(self.owner.rows.items()))


class FakeSessionFactory:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.queries = 0
        self.error: Exception | None = None

    def __call__(self):
        return FakeSession(self)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def test_reads_flags_and_defaults_missing_rows_to_off():
    factory = FakeSessionFactory({"ent_ledger_write": True, "ent_read_api": False})
    reader = FeatureFlagReader(factory, clock=Clock())

    assert await reader.is_enabled("ent_ledger_write") is True
    assert await reader.is_enabled("ent_read_api") is False
    assert await reader.is_enabled("ent_reconcile") is False
    assert await reader.is_enabled("ent_reconcile", default=True) is True
    assert factory.queries == 1


async def test_cache_expires_after_ttl():
    factory = FakeSessionFactory({"ent_ledger_write": False})
    clock = Clock()
    reader = FeatureFlagReader(factory, ttl_seconds=30, clock=clock)
    assert await reader.is_enabled("ent_ledger_write") is False

    factory.rows["ent_ledger_write"] = True
    clock.now = 29.9
    assert await reader.is_enabled("ent_ledger_write") is False
    clock.now = 30.0
    assert await reader.is_enabled("ent_ledger_write") is True
    assert factory.queries == 2


async def test_read_error_returns_default_and_backs_off():
    factory = FakeSessionFactory({"ent_ledger_write": True})
    factory.error = RuntimeError("db down")
    clock = Clock()
    reader = FeatureFlagReader(factory, failure_backoff_seconds=5, clock=clock)

    assert await reader.is_enabled("ent_ledger_write") is False
    assert await reader.is_enabled("ent_ledger_write", default=True) is True
    assert factory.queries == 1

    factory.error = None
    clock.now = 5.0
    assert await reader.is_enabled("ent_ledger_write") is True


async def test_session_factory_failure_is_swallowed():
    def broken_factory():
        raise RuntimeError("AsyncSessionLocal is not initialized")

    reader = FeatureFlagReader(broken_factory, clock=Clock())

    assert await reader.is_enabled("ent_read_api") is False


async def test_invalidate_forces_reload():
    factory = FakeSessionFactory({"ent_read_api": False})
    reader = FeatureFlagReader(factory, clock=Clock())
    await reader.is_enabled("ent_read_api")
    factory.rows["ent_read_api"] = True

    reader.invalidate()

    assert await reader.is_enabled("ent_read_api") is True
