"""Focused contract tests for durable visual body-fat profile selections."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes.v1.user_profiles import (
    get_body_fat_visual_profile,
    save_body_fat_visual_profile,
)
from src.api.schemas.request.body_fat_visual_requests import BodyFatVisualProfileRequest
from src.app.commands.user.save_body_fat_visual_profile_command import (
    SaveBodyFatVisualProfileCommand,
)
from src.app.handlers.command_handlers.save_body_fat_visual_profile_command_handler import (
    SaveBodyFatVisualProfileCommandHandler,
)
from src.app.handlers.query_handlers.get_body_fat_visual_profile_query_handler import (
    GetBodyFatVisualProfileQueryHandler,
)
from src.app.queries.user.get_body_fat_visual_profile_query import (
    GetBodyFatVisualProfileQuery,
)
from src.domain.model.user.body_fat_visual import (
    BODY_FAT_VISUAL_RANGES_BY_SEX,
    BodyFatVisualProfileSelection,
    remap_visual_profile_selection,
)
from src.infra.database.models.user.body_fat_visual_profile import BodyFatVisualProfile


def valid_payload(**overrides):
    payload = {
        "schema_version": 1,
        "range_catalog_version": 1,
        "sex_at_selection": "male",
        "start_range_id": None,
        "current_range_id": "male_17_20",
        "target_range_id": "male_13_16",
    }
    payload.update(overrides)
    return payload


def test_visual_body_fat_request_accepts_exact_catalog_values():
    request = BodyFatVisualProfileRequest(**valid_payload())

    assert request.current_range_id == "male_17_20"


@pytest.mark.parametrize(
    ("source_sex", "target_sex", "current_range_id", "expected_range_id"),
    [
        ("male", "female", "male_21_24", "female_31_35"),
        ("female", "male", "female_36_39", "male_25_29"),
    ],
)
def test_visual_profile_remaps_ranges_by_stable_catalog_ordinal(
    source_sex, target_sex, current_range_id, expected_range_id
):
    selection = BodyFatVisualProfileSelection(
        user_id="user-1",
        schema_version=1,
        range_catalog_version=1,
        sex_at_selection=source_sex,
        start_range_id=None,
        current_range_id=current_range_id,
        target_range_id=None,
    )

    remapped = remap_visual_profile_selection(selection, target_sex=target_sex)

    assert len(BODY_FAT_VISUAL_RANGES_BY_SEX["male"]) == len(
        BODY_FAT_VISUAL_RANGES_BY_SEX["female"]
    )
    assert remapped.sex_at_selection == target_sex
    assert remapped.start_range_id is None
    assert remapped.current_range_id == expected_range_id
    assert remapped.target_range_id is None


def test_visual_body_fat_request_allows_omitting_target_range():
    request = BodyFatVisualProfileRequest(**valid_payload(target_range_id=None))

    assert request.target_range_id is None
    assert request.range_catalog_version == 1
    assert isinstance(request.range_catalog_version, int)


def test_visual_body_fat_request_accepts_start_range():
    request = BodyFatVisualProfileRequest(**valid_payload(start_range_id="male_21_24"))

    assert request.start_range_id == "male_21_24"


@pytest.mark.parametrize(
    "payload",
    [
        valid_payload(schema_version=2),
        valid_payload(range_catalog_version=2),
        valid_payload(start_range_id="female_22_25"),
        valid_payload(current_range_id="female_22_25"),
        valid_payload(target_range_id="male_5_7"),
        valid_payload(unexpected=True),
    ],
)
def test_visual_body_fat_request_rejects_invalid_contract_values(payload):
    with pytest.raises(ValidationError):
        BodyFatVisualProfileRequest(**payload)


@pytest.mark.asyncio
async def test_put_appends_selection_then_returns_latest_history():
    request = BodyFatVisualProfileRequest(**valid_payload())
    expected = {**request.model_dump(), "updated_at": datetime.now(UTC), "history": []}
    event_bus = MagicMock()
    event_bus.send = AsyncMock(side_effect=[None, expected])

    response = await save_body_fat_visual_profile(
        request, user_id="user-1", event_bus=event_bus
    )

    assert response == expected
    assert event_bus.send.await_args_list[0].args[0] == SaveBodyFatVisualProfileCommand(
        user_id="user-1", **request.model_dump()
    )
    assert event_bus.send.await_args_list[1].args[0] == GetBodyFatVisualProfileQuery(
        user_id="user-1"
    )


@pytest.mark.asyncio
async def test_get_returns_404_when_no_selection_exists():
    event_bus = MagicMock()
    event_bus.send = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await get_body_fat_visual_profile(user_id="user-1", event_bus=event_bus)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {
        "error_code": "BODY_FAT_VISUAL_UNSET",
        "message": "No visual body-fat selection has been saved",
        "details": {},
    }
    assert event_bus.send.await_args.args[0] == GetBodyFatVisualProfileQuery("user-1")


@pytest.mark.asyncio
async def test_save_handler_appends_separate_visual_record_only():
    command = SaveBodyFatVisualProfileCommand(user_id="user-1", **valid_payload())
    uow = MagicMock()
    uow.body_fat_visual_profiles.append = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    await SaveBodyFatVisualProfileCommandHandler(uow=uow).handle(command)

    record = uow.body_fat_visual_profiles.append.await_args.args[0]
    assert isinstance(record, BodyFatVisualProfileSelection)
    assert record.user_id == "user-1"
    assert record.start_range_id is None
    assert record.current_range_id == "male_17_20"


def test_database_target_constraint_allows_null_and_validates_present_ranges():
    assert BodyFatVisualProfile.__table__.c.start_range_id.nullable
    assert BodyFatVisualProfile.__table__.c.target_range_id.nullable

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in BodyFatVisualProfile.__table__.constraints
        if constraint.name
    }

    assert "start_range_id IN" in constraints["check_bf_visual_start_range"]
    assert "target_range_id IN" in constraints["check_bf_visual_target_range"]
    assert (
        "start_range_id IS NULL OR start_range_id LIKE 'male_%'"
        in constraints["check_bf_visual_ranges_match_sex"]
    )
    assert (
        "target_range_id IS NULL OR target_range_id LIKE 'male_%'"
        in constraints["check_bf_visual_ranges_match_sex"]
    )


def test_query_serialization_keeps_selection_versions_and_timestamp():
    timestamp = datetime(2026, 7, 16, tzinfo=UTC)
    record = MagicMock(
        schema_version=1,
        range_catalog_version=1,
        sex_at_selection="female",
        start_range_id="female_36_39",
        current_range_id="female_31_35",
        target_range_id="female_26_30",
        updated_at=timestamp,
    )

    assert GetBodyFatVisualProfileQueryHandler._serialize(record) == {
        "schema_version": 1,
        "range_catalog_version": 1,
        "sex_at_selection": "female",
        "start_range_id": "female_36_39",
        "current_range_id": "female_31_35",
        "target_range_id": "female_26_30",
        "updated_at": timestamp,
    }


def test_query_serialization_allows_no_target_range():
    record = MagicMock(
        schema_version=1,
        range_catalog_version=1,
        sex_at_selection="male",
        start_range_id=None,
        current_range_id="male_17_20",
        target_range_id=None,
        updated_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    assert (
        GetBodyFatVisualProfileQueryHandler._serialize(record)["target_range_id"]
        is None
    )
