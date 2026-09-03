"""Chat response DTOs."""

from typing import Any

from pydantic import BaseModel, Field


class ChatThreadSummaryResponse(BaseModel):
    id: str
    created_at: str
    updated_at: str


class ChatCitationResponse(BaseModel):
    label: str | None = None
    source_key: str
    title: str | None = None
    canonical_uri: str | None = None


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str | None
    created_at: str
    status: str = "completed"
    model: str | None = None
    citation_source_keys: list[str] = Field(default_factory=list)
    citations: list[ChatCitationResponse] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    follow_ups: list[dict[str, Any]] = Field(default_factory=list)


class ChatInFlightResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message_id: str
    idempotency_key: str | None = None
    lease_expires_at: str | None = None


class ChatThreadResponse(BaseModel):
    thread: ChatThreadSummaryResponse
    messages: list[ChatMessageResponse]
    has_more: bool = False
    in_flight: ChatInFlightResponse | None = None


class ChatClearResponse(BaseModel):
    thread_id: str
    cleared: bool = True
