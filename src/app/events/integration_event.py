"""Versioned integration events sent from the backend to async consumers."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INTEGRATION_EVENT_VERSION = 1
MAX_INTEGRATION_EVENT_BYTES = 32 * 1024
HYDRATION_CREATED_EVENT_TYPE = "hydration.created.v1"


class IntegrationEvent(BaseModel):
    """Common wire envelope for cross-repository events.

    This is intentionally separate from the internal PyMediator DomainEvent
    hierarchy. Its JSON representation is the compatibility boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    event_type: str = Field(min_length=1, max_length=128)
    event_id: str = Field(
        default_factory=lambda: str(uuid4()), min_length=1, max_length=255
    )
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: Literal["mealtrack_backend"] = "mealtrack_backend"
    environment: str = Field(default="development", min_length=1, max_length=32)
    aggregate_type: str = Field(min_length=1, max_length=64)
    aggregate_id: str = Field(min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=255)
    causation_id: str | None = Field(default=None, max_length=255)
    data: Any

    @model_validator(mode="after")
    def validate_wire_size(self) -> IntegrationEvent:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_INTEGRATION_EVENT_BYTES:
            raise ValueError(
                f"integration event exceeds {MAX_INTEGRATION_EVENT_BYTES} bytes"
            )
        return self

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible payload stored in the outbox."""
        return self.model_dump(mode="json")


class HydrationCreatedData(BaseModel):
    """Minimal immutable snapshot needed by hydration consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    hydration_id: str = Field(pattern=r"^hydr_[0-9a-f]{32}$", max_length=37)
    meal_id: str = Field(min_length=1, max_length=128)
    drink_id: str = Field(min_length=1, max_length=128)
    drink_name: str = Field(min_length=1, max_length=255)
    emoji: str | None = Field(default=None, max_length=32)
    volume_ml: int = Field(gt=0, le=100_000)
    credited_ml: int = Field(ge=0, le=100_000)
    logged_at: datetime
    log_date: date
    source: Literal["hydration"] = "hydration"

    @field_validator("user_id")
    @classmethod
    def user_id_must_be_uuid(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("user_id must be a UUID") from exc
        return value


class HydrationCreatedEvent(IntegrationEvent):
    """Published after a hydration and its legacy meal are persisted."""

    event_type: str = HYDRATION_CREATED_EVENT_TYPE
    aggregate_type: str = "hydration"
    data: HydrationCreatedData

    @field_validator("event_id")
    @classmethod
    def event_id_must_be_uuid(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("event_id must be a UUID") from exc
        return value

    @model_validator(mode="after")
    def aggregate_matches_snapshot(self) -> HydrationCreatedEvent:
        if self.event_type != HYDRATION_CREATED_EVENT_TYPE:
            raise ValueError("event_type must be hydration.created.v1")
        if self.aggregate_type != "hydration":
            raise ValueError("aggregate_type must be hydration")
        if self.aggregate_id != self.data.hydration_id:
            raise ValueError("aggregate_id must match data.hydration_id")
        return self
