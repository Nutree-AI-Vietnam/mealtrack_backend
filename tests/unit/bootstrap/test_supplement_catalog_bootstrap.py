"""Only a unique clash during review becomes a retry-able 409."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.services.supplement_catalog_review import ReviewAction
from src.bootstrap import supplement_catalog as bootstrap
from src.domain.model.supplement.catalog_review import SupplementReviewConflictError


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
