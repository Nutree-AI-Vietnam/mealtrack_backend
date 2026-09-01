"""Chat response DTOs."""

from pydantic import BaseModel, Field


class ChatThreadSummaryResponse(BaseModel):
    id: str
    created_at: str
    updated_at: str


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str | None
    created_at: str
    model: str | None = None
    citation_source_keys: list[str] = Field(default_factory=list)


class ChatThreadResponse(BaseModel):
    thread: ChatThreadSummaryResponse
    messages: list[ChatMessageResponse]
    has_more: bool = False


class ChatClearResponse(BaseModel):
    thread_id: str
    cleared: bool = True
