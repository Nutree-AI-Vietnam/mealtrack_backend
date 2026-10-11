import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "migrate_cloudinary_to_cloudflare.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "migrate_cloudinary_to_cloudflare", _SCRIPT_PATH
)
assert _SPEC is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
migrate_single_image = _MODULE.migrate_single_image


class _FakeSession:
    def __init__(self):
        self.executed_statements = []

    async def execute(self, statement):
        self.executed_statements.append(statement)


@pytest.mark.asyncio
async def test_migrate_single_image_skips_db_update_on_empty_or_invalid_url():
    fake_img = SimpleNamespace(
        image_id="img-empty-url",
        url="https://res.cloudinary.com/demo/image/upload/v1/sample.jpg",
        format="jpeg",
    )

    fake_session = _FakeSession()
    mock_cf_store = MagicMock()
    mock_cf_store.save_async = AsyncMock(return_value="")  # Returns empty URL

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.content = b"fake-bytes"
    mock_http_resp.headers = {"content-type": "image/jpeg"}

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_http_resp)

    success = await migrate_single_image(
        fake_img,
        cf_store=mock_cf_store,
        session=fake_session,
        http_client=mock_http_client,
    )

    assert success is False
    # Ensure no UPDATE statement was executed against the database
    assert len(fake_session.executed_statements) == 0


@pytest.mark.asyncio
async def test_migrate_single_image_updates_db_on_valid_url():
    fake_img = SimpleNamespace(
        image_id="img-valid",
        url="https://res.cloudinary.com/demo/image/upload/v1/sample.jpg",
        format="jpeg",
    )

    fake_session = _FakeSession()
    mock_cf_store = MagicMock()
    mock_cf_store.save_async = AsyncMock(
        return_value="https://imagedelivery.net/hash/img-valid/public"
    )

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.content = b"fake-bytes"
    mock_http_resp.headers = {"content-type": "image/jpeg"}

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_http_resp)

    success = await migrate_single_image(
        fake_img,
        cf_store=mock_cf_store,
        session=fake_session,
        http_client=mock_http_client,
    )

    assert success is True
    assert len(fake_session.executed_statements) == 1


@pytest.mark.asyncio
async def test_migrate_single_image_fails_safely_on_download_error():
    fake_img = SimpleNamespace(
        image_id="img-fail",
        url="https://res.cloudinary.com/demo/image/upload/v1/missing.jpg",
        format="jpeg",
    )

    fake_session = _FakeSession()
    mock_cf_store = MagicMock()
    mock_cf_store.save_async = AsyncMock()

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 404
    mock_http_resp.content = b"Not Found"

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_http_resp)

    success = await migrate_single_image(
        fake_img,
        cf_store=mock_cf_store,
        session=fake_session,
        http_client=mock_http_client,
    )

    assert success is False
    assert mock_cf_store.save_async.call_count == 0
    assert len(fake_session.executed_statements) == 0
