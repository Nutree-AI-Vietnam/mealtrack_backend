"""Chat request DTOs."""

from pydantic import BaseModel, Field, field_validator

from src.domain.model.chat import CHAT_MAX_USER_MESSAGE_CHARS


class ChatMessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=CHAT_MAX_USER_MESSAGE_CHARS)
    locale: str | None = Field(default=None, max_length=8)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped
