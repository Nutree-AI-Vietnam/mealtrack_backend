"""Command for analyzing an uploaded image via the bytes-download path."""

from dataclasses import dataclass, field
from datetime import date, datetime

from src.app.events.base import Command
from src.domain.utils.timezone_utils import utc_now


@dataclass
class ScanByUrlCommand(Command):
    """Analyze an uploaded image without sending the URL to the AI provider directly."""

    user_id: str
    image_url: str
    public_id: str
    user_description: str | None = None
    target_date: date | None = None
    language: str = "en"
    scan_mode: str = "scanner"
    label_crop_image_url: str | None = None
    label_crop_public_id: str | None = None
    crop_metadata: dict | None = None
    # Set by background jobs so a retried attempt saves the same meal row.
    meal_id: str | None = None
    # When the request arrived (a background job's enqueue time): decides
    # whether the log is for its own day, however long the analysis takes.
    received_at: datetime = field(default_factory=utc_now)
