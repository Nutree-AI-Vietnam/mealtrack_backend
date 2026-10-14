"""Chat response DTOs."""

from typing import Any

from pydantic import BaseModel, Field


class ChatThreadSummaryResponse(BaseModel):
    id: str
    created_at: str
    updated_at: str


class ChatThreadListItemResponse(ChatThreadSummaryResponse):
    """A thread in the list; labels come from stored messages, never generated."""

    title: str | None = Field(
        default=None, description="The thread's first question, shortened."
    )
    preview: str | None = Field(
        default=None, description="The thread's latest message, shortened."
    )
    preview_role: str | None = Field(
        default=None, description="Who wrote the preview: user or assistant."
    )
    last_message_at: str | None = None


class ChatThreadListResponse(BaseModel):
    threads: list[ChatThreadListItemResponse]
    has_more: bool = False
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to read the next page."
    )


class ChatCitationResponse(BaseModel):
    label: str | None = None
    source_key: str
    title: str | None = None
    canonical_uri: str | None = None


class ChatNutritionMacroResponse(BaseModel):
    consumed_g: float | None = None
    target_g: float | None = None
    remaining_g: float | None = None


class ChatNutritionMacrosResponse(BaseModel):
    protein: ChatNutritionMacroResponse | None = None
    carbs: ChatNutritionMacroResponse | None = None
    fat: ChatNutritionMacroResponse | None = None


class ChatNutritionSnapshotResponse(BaseModel):
    version: str | None = None
    as_of: str | None = None
    local_date: str | None = None
    timezone: str | None = None
    target_calories: float | None = None
    food_calories: float | None = None
    movement_kcal_burned: float | None = None
    remaining_calories: float | None = None
    remaining_days: int | None = None
    macros: ChatNutritionMacrosResponse | None = None


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
    intent: str | None = None
    discover_session_id: str | None = None
    nutrition_snapshot: ChatNutritionSnapshotResponse | None = None


class ChatInFlightResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message_id: str
    idempotency_key: str | None = None
    lease_expires_at: str | None = None
    ai_job_id: str | None = Field(
        default=None, description="The background job generating this reply"
    )


class ChatWelcomeResponse(BaseModel):
    preferred_name: str | None = None
    remaining_calories: int | None = None
    remaining_protein_g: int | None = None
    remaining_carbs_g: int | None = None
    suggested_meal_slot: str | None = None
    latest_meal_name: str | None = None


class ChatThreadResponse(BaseModel):
    thread: ChatThreadSummaryResponse
    messages: list[ChatMessageResponse]
    has_more: bool = False
    in_flight: ChatInFlightResponse | None = None
    welcome: ChatWelcomeResponse | None = None


class ChatClearResponse(BaseModel):
    thread_id: str
    cleared: bool = True
