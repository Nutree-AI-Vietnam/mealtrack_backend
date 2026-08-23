"""Versioned integration-event envelope shared by app and infrastructure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INTEGRATION_EVENT_VERSION = 1
MAX_INTEGRATION_EVENT_BYTES = 32 * 1024


class IntegrationEvent(BaseModel):
    """Common wire envelope for cross-repository events."""

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
    data: dict[str, Any] | None = Field(default=None)

    @field_validator("event_id")
    @classmethod
    def event_id_must_be_uuid(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("event_id must be a UUID") from exc
        return value

    @model_validator(mode="after")
    def validate_wire_size(self) -> IntegrationEvent:
        payload = self.model_dump(mode="json", exclude_none=True)
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_INTEGRATION_EVENT_BYTES:
            raise ValueError(
                f"integration event exceeds {MAX_INTEGRATION_EVENT_BYTES} bytes"
            )
        return self

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible payload stored in the outbox."""
        return self.model_dump(mode="json", exclude_none=True)
