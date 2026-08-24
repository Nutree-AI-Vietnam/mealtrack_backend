"""Cover src.api.main: Firebase init branches and lifespan error paths."""
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _reload_main():
    """Fresh import so initialize_firebase is the real function (not stubbed by other tests)."""
    sys.modules.pop("src.api.main", None)
    return importlib.import_module("src.api.main")


def _patch_lifespan_side_effects(main_mod):
    main_mod.initialize_firebase = lambda: None  # type: ignore[assignment]

    async def _noop():
        return None

    main_mod.warm_database_connection = _noop  # type: ignore[assignment]
    main_mod.initialize_cache_layer = _noop  # type: ignore[assignment]
    main_mod.shutdown_cache_layer = _noop  # type: ignore[assignment]
    # Note: no scheduler stub — scheduler removed from lifespan


@pytest.fixture
def fresh_main(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("FAIL_ON_CACHE_ERROR", raising=False)
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    sys.modules.pop("src.api.main", None)
    m = importlib.import_module("src.api.main")
    _patch_lifespan_side_effects(m)
    return m


def test_lifespan_firebase_failure_propagates_with_critical_log(
    monkeypatch, fresh_main, caplog
):
    fresh_main.initialize_firebase = lambda: (_ for _ in ()).throw(  # type: ignore[assignment]
        RuntimeError("firebase down")
    )
    with caplog.at_level("CRITICAL"):
        with pytest.raises(RuntimeError, match="firebase down"):
            with TestClient(fresh_main.app):
                pass

    assert any(
        record.levelname == "CRITICAL" and "aborting startup" in record.message
        for record in caplog.records
    )


def test_lifespan_cache_failure_raises_when_env_true_with_critical_log(
    monkeypatch, fresh_main, caplog
):
    monkeypatch.setenv("FAIL_ON_CACHE_ERROR", "true")

    async def boom():
        raise RuntimeError("cache")

    fresh_main.initialize_cache_layer = boom  # type: ignore[assignment]

    with caplog.at_level("CRITICAL"):
        with pytest.raises(RuntimeError, match="cache"):
            with TestClient(fresh_main.app):
                pass

    assert any(
        record.levelname == "CRITICAL" and "Cache layer is required" in record.message
        for record in caplog.records
    )


def test_lifespan_cache_failure_without_fail_fast_does_not_log_critical(
    monkeypatch, fresh_main, caplog
):
    monkeypatch.setenv("FAIL_ON_CACHE_ERROR", "false")

    async def boom():
        raise RuntimeError("cache")

    fresh_main.initialize_cache_layer = boom  # type: ignore[assignment]

    with caplog.at_level("ERROR"):
        with TestClient(fresh_main.app):
            pass

    assert any(record.levelname == "ERROR" for record in caplog.records)
    assert all(record.levelname != "CRITICAL" for record in caplog.records)


def test_initialize_firebase_already_initialized(monkeypatch):
    main = _reload_main()

    monkeypatch.setattr(main.firebase_admin, "get_app", lambda: MagicMock(name="app"))
    main.initialize_firebase()


def test_initialize_firebase_default_credentials(monkeypatch):
    main = _reload_main()

    def _not_init():
        raise ValueError("not init")

    monkeypatch.setattr(main.firebase_admin, "get_app", _not_init)
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    init = MagicMock()
    monkeypatch.setattr(main.firebase_admin, "initialize_app", init)
    main.initialize_firebase()
    init.assert_called_once_with()


def test_initialize_firebase_from_json_string(monkeypatch):
    main = _reload_main()

    def _not_init():
        raise ValueError("not init")

    monkeypatch.setattr(main.firebase_admin, "get_app", _not_init)
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    monkeypatch.setenv(
        "FIREBASE_SERVICE_ACCOUNT_JSON",
        '{"type":"service_account","project_id":"p"}',
    )
    init = MagicMock()
    cert = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(main.firebase_admin, "initialize_app", init)
    with patch.object(main.credentials, "Certificate", cert):
        main.initialize_firebase()
    init.assert_called_once()
    cert.assert_called_once_with(
        {"type": "service_account", "project_id": "p"}
    )


def test_initialize_firebase_invalid_json_string(monkeypatch):
    main = _reload_main()

    def _not_init():
        raise ValueError("not init")

    monkeypatch.setattr(main.firebase_admin, "get_app", _not_init)
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "not-json")
    with pytest.raises(ValueError, match="invalid JSON"):
        main.initialize_firebase()


def test_initialize_firebase_from_credentials_file(monkeypatch, tmp_path):
    main = _reload_main()

    cred_path = tmp_path / "sa.json"
    cred_path.write_text('{"type":"service_account","project_id":"x"}', encoding="utf-8")

    def _not_init():
        raise ValueError("not init")

    monkeypatch.setattr(main.firebase_admin, "get_app", _not_init)
    monkeypatch.setenv("FIREBASE_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    init = MagicMock()
    cert = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(main.firebase_admin, "initialize_app", init)
    with patch.object(main.credentials, "Certificate", cert):
        main.initialize_firebase()
    cert.assert_called_once_with(str(cred_path))
    init.assert_called_once()


@pytest.mark.asyncio
async def test_warm_database_connection_uses_async_engine(monkeypatch):
    main = _reload_main()

    class _ConnectionContext:
        def __init__(self):
            self.connection = MagicMock()
            self.connection.execute = AsyncMock()
            self.connection.commit = AsyncMock()

        async def __aenter__(self):
            return self.connection

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _AsyncEngine:
        def __init__(self):
            self.context = _ConnectionContext()

        def connect(self):
            return self.context

    engine = _AsyncEngine()
    monkeypatch.setattr(main, "async_engine", engine)

    await main.warm_database_connection()

    engine.context.connection.execute.assert_awaited_once()
    engine.context.connection.commit.assert_awaited_once()


def test_development_static_uploads_mount(tmp_path, monkeypatch):
    from starlette.routing import Mount

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    (tmp_path / "placeholder.txt").write_text("ok", encoding="utf-8")
    sys.modules.pop("src.api.main", None)
    m = importlib.import_module("src.api.main")
    _patch_lifespan_side_effects(m)
    assert any(isinstance(r, Mount) and r.path == "/uploads" for r in m.app.routes)
