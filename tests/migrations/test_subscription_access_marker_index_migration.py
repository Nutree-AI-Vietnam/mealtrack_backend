"""The marker index is built and dropped concurrently, outside the migration
transaction, so the build never blocks writes to subscriptions."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest

MIGRATION = Path(
    "migrations/versions/20261028142458697212_add_subscription_access_marker_index.py"
)


class _RecordingOp:
    """Stands in for alembic.op: records each call and whether it ran inside
    autocommit_block()."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, tuple, dict]] = []
        self._autocommit = False

    def get_context(self) -> "_RecordingOp":
        return self

    @contextmanager
    def autocommit_block(self):
        self._autocommit = True
        try:
            yield
        finally:
            self._autocommit = False

    def create_index(self, *args, **kwargs) -> None:
        self.calls.append(("create_index", self._autocommit, args, kwargs))

    def drop_index(self, *args, **kwargs) -> None:
        self.calls.append(("drop_index", self._autocommit, args, kwargs))


@pytest.fixture
def migration(monkeypatch):
    spec = importlib.util.spec_from_file_location("marker_index_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    op = _RecordingOp()
    monkeypatch.setattr(module, "op", op)
    return module, op


@pytest.mark.parametrize("step", ["upgrade", "downgrade"])
def test_marker_index_is_built_and_dropped_concurrently(migration, step):
    module, op = migration

    getattr(module, step)()

    ((name, autocommit, args, kwargs),) = op.calls
    assert name == ("create_index" if step == "upgrade" else "drop_index")
    assert args[0] == "ix_subscriptions_access_markers"
    assert autocommit, "CONCURRENTLY cannot run inside a transaction"
    assert kwargs["postgresql_concurrently"] is True
    assert str(kwargs["postgresql_where"]) == module.MARKED
