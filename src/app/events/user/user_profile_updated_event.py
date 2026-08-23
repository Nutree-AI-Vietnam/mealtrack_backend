"""
User profile updated event.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from src.app.events.base import DomainEvent


@dataclass
class UserProfileUpdatedEvent(DomainEvent):
    """Event raised when user profile is updated."""

    aggregate_id: str
    profile_id: str
    updated_fields: list[str]
    old_tdee: float | None = None
    new_tdee: float | None = None
    # Metadata fields with defaults
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

