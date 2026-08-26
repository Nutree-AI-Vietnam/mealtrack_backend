"""Validate and resolve client-supplied meal and food-item UUIDs."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from uuid import uuid4

from src.api.exceptions import ConflictException, ValidationException
from src.domain.ports.meal_repository_port import MealRepositoryPort


def parse_optional_client_uuid(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    try:
        return str(uuid.UUID(trimmed))
    except ValueError as exc:
        raise ValidationException(
            f"Invalid {field}: must be a UUID",
            error_code="INVALID_CLIENT_RESOURCE_ID",
            details={field: value},
        ) from exc


def assert_unique_command_item_ids(items: Iterable) -> None:
    seen: set[str] = set()
    for item in items:
        raw_id = getattr(item, "id", None)
        if not raw_id:
            continue
        parsed = parse_optional_client_uuid(raw_id, field="item id")
        assert parsed is not None
        if parsed in seen:
            raise ValidationException(
                "Duplicate food item ids in request",
                error_code="DUPLICATE_CLIENT_ITEM_ID",
            )
        seen.add(parsed)


async def resolve_client_meal_id(
    *,
    requested_meal_id: str | None,
    user_id: str,
    meal_repo: MealRepositoryPort,
) -> str:
    meal_id = parse_optional_client_uuid(requested_meal_id, field="meal_id")
    if meal_id is None:
        return str(uuid4())

    existing = await meal_repo.find_by_id(meal_id)
    if existing is None:
        return meal_id

    if existing.user_id != user_id:
        raise ConflictException(
            "Meal id is already in use by another account",
            error_code="CLIENT_MEAL_ID_CONFLICT",
            details={"meal_id": meal_id},
        )

    raise ConflictException(
        "Meal id already exists",
        error_code="CLIENT_MEAL_ID_CONFLICT",
        details={"meal_id": meal_id},
    )
