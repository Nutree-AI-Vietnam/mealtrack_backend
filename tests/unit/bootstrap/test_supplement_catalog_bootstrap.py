"""Catalog transactions: review conflicts to retry, and photo cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.services.supplement_catalog_review import ReviewAction
from src.app.services.supplement_product_photo import StoredPhoto
from src.bootstrap import supplement_catalog as bootstrap
from src.domain.model.supplement.catalog_review import (
    SupplementNotReviewableError,
    SupplementReviewConflictError,
)


class _Uow:
    def __init__(self):
        self.supplement_catalog = SimpleNamespace(
            schema_ready=AsyncMock(return_value=True)
        )
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _clash(sqlstate: str) -> IntegrityError:
    return IntegrityError("UPDATE …", {}, SimpleNamespace(sqlstate=sqlstate))


@pytest.mark.asyncio
async def test_a_unique_clash_is_a_conflict_to_retry(monkeypatch):
    monkeypatch.setattr(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=_Uow()))
    monkeypatch.setattr(
        bootstrap, "review_product", AsyncMock(side_effect=_clash("23505"))
    )

    with pytest.raises(SupplementReviewConflictError):
        await bootstrap.review_supplement("p1", ReviewAction.APPROVE, "a@x")


@pytest.mark.asyncio
async def test_any_other_integrity_error_is_not_hidden_as_a_retry(monkeypatch):
    monkeypatch.setattr(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=_Uow()))
    monkeypatch.setattr(
        bootstrap, "review_product", AsyncMock(side_effect=_clash("23514"))
    )

    with pytest.raises(IntegrityError):
        await bootstrap.review_supplement("p1", ReviewAction.APPROVE, "a@x")


@pytest.mark.asyncio
async def test_a_new_photo_replaces_the_old_one_after_commit(monkeypatch):
    photo = StoredPhoto("new-id", "https://img.test/new.jpg")
    monkeypatch.setattr(bootstrap, "store_photo", AsyncMock(return_value=photo))
    monkeypatch.setattr(
        bootstrap, "_point_at", AsyncMock(return_value=("item", "old-id"))
    )
    delete = AsyncMock()
    monkeypatch.setattr(bootstrap, "delete_photo", delete)

    item = await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    assert item == "item"
    assert [c.args[1] for c in delete.await_args_list] == ["old-id"]


@pytest.mark.asyncio
async def test_a_photo_the_row_never_took_is_deleted(monkeypatch):
    photo = StoredPhoto("new-id", "https://img.test/new.jpg")
    monkeypatch.setattr(bootstrap, "store_photo", AsyncMock(return_value=photo))
    monkeypatch.setattr(
        bootstrap,
        "_point_at",
        AsyncMock(side_effect=SupplementNotReviewableError("approve the scan")),
    )
    delete = AsyncMock()
    monkeypatch.setattr(bootstrap, "delete_photo", delete)

    with pytest.raises(SupplementNotReviewableError):
        await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    assert [c.args[1] for c in delete.await_args_list] == ["new-id"]


@pytest.mark.asyncio
async def test_removing_a_photo_deletes_it_after_commit(monkeypatch):
    monkeypatch.setattr(
        bootstrap, "_point_at", AsyncMock(return_value=("item", "old-id"))
    )
    delete = AsyncMock()
    monkeypatch.setattr(bootstrap, "delete_photo", delete)

    await bootstrap.remove_supplement_photo("p1", object())

    assert [c.args[1] for c in delete.await_args_list] == ["old-id"]
