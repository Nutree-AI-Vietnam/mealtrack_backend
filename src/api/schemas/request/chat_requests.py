"""Chat request DTOs."""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.domain.model.chat import CHAT_MAX_USER_MESSAGE_CHARS, ChatIntent


class ChatToolCallRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class ChatMessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=CHAT_MAX_USER_MESSAGE_CHARS)
    locale: str | None = Field(default=None, max_length=8)
    intent: ChatIntent | None = None
    tool: ChatToolCallRequest | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped
