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


def _photo_steps(monkeypatch, point_at):
    """Stub the pre-check, the upload and the write; return the delete mock."""
    monkeypatch.setattr(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=_Uow()))
    monkeypatch.setattr(bootstrap, "check_photo_target", AsyncMock())
    photo = StoredPhoto("new-id", "https://img.test/new.jpg")
    monkeypatch.setattr(bootstrap, "store_photo", AsyncMock(return_value=photo))
    monkeypatch.setattr(bootstrap, "_point_at", point_at)
    delete = AsyncMock()
    monkeypatch.setattr(bootstrap, "delete_photo", delete)
    return delete


def _row_pointing_at(image_id):
    product = SimpleNamespace(image_id=image_id)
    return SimpleNamespace(product=product)


@pytest.mark.asyncio
async def test_a_new_photo_replaces_the_old_one_after_commit(monkeypatch):
    delete = _photo_steps(monkeypatch, AsyncMock(return_value=("item", "old-id")))

    item = await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    assert item == "item"
    assert [c.args[1] for c in delete.await_args_list] == ["old-id"]


@pytest.mark.asyncio
async def test_a_refused_product_costs_no_upload(monkeypatch):
    _photo_steps(monkeypatch, AsyncMock())
    monkeypatch.setattr(
        bootstrap,
        "check_photo_target",
        AsyncMock(side_effect=SupplementNotReviewableError("approve the scan")),
    )

    with pytest.raises(SupplementNotReviewableError):
        await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    bootstrap.store_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_photo_the_row_never_took_is_deleted(monkeypatch):
    delete = _photo_steps(
        monkeypatch, AsyncMock(side_effect=SupplementNotReviewableError("raced"))
    )
    uow = _Uow()
    uow.supplement_catalog.review_item = AsyncMock(
        return_value=_row_pointing_at("old-id")
    )
    monkeypatch.setattr(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=uow))

    with pytest.raises(SupplementNotReviewableError):
        await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    assert [c.args[1] for c in delete.await_args_list] == ["new-id"]


@pytest.mark.asyncio
async def test_a_photo_is_kept_when_the_commit_landed_despite_the_error(monkeypatch):
    delete = _photo_steps(
        monkeypatch, AsyncMock(side_effect=ConnectionError("lost during COMMIT"))
    )
    uow = _Uow()
    uow.supplement_catalog.review_item = AsyncMock(
        return_value=_row_pointing_at("new-id")
    )
    monkeypatch.setattr(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=uow))

    with pytest.raises(ConnectionError):
        await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_photo_is_kept_when_the_row_cannot_be_read_back(monkeypatch):
    delete = _photo_steps(
        monkeypatch, AsyncMock(side_effect=ConnectionError("database gone"))
    )
    uow = _Uow()
    uow.supplement_catalog.review_item = AsyncMock(side_effect=ConnectionError())
    monkeypatch.setattr(bootstrap, "AsyncUnitOfWork", MagicMock(return_value=uow))

    with pytest.raises(ConnectionError):
        await bootstrap.set_supplement_photo("p1", b"jpeg", object())

    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_removing_a_photo_deletes_it_after_commit(monkeypatch):
    monkeypatch.setattr(
        bootstrap, "_point_at", AsyncMock(return_value=("item", "old-id"))
    )
    delete = AsyncMock()
    monkeypatch.setattr(bootstrap, "delete_photo", delete)

    await bootstrap.remove_supplement_photo("p1", object())

    assert [c.args[1] for c in delete.await_args_list] == ["old-id"]
