"""Request for POST /v1/log/describe (the + panel's Ghi nhanh)."""

from typing import Literal

from pydantic import BaseModel, Field

LogKind = Literal["food", "drink", "activity", "supplement"]


class DescribeAnyRequest(BaseModel):
    """One sentence that may log food, drinks, activities and supplements."""

    text: str = Field(..., min_length=1, max_length=300, description="The sentence")
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=160,
        description=(
            "Chosen by the app per sentence: a retry with the same id replays "
            "the saved reply, and DELETE /v1/log/describe/{request_id} undoes it"
        ),
    )
    local_date: str | None = Field(
        None, description="The day to log, YYYY-MM-DD; the person's today by default"
    )
    local_time: str | None = Field(
        None, description="The phone's clock, HH:MM, which picks the meal type"
    )
    kinds: list[LogKind] | None = Field(
        None,
        min_length=1,
        description='Kinds to save, e.g. ["supplement"]; every kind by default',
    )
