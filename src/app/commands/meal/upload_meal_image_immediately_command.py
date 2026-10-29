"""
Command for immediate meal image upload and analysis.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from src.app.events.base import Command
from src.domain.utils.timezone_utils import utc_now


@dataclass
class UploadMealImageImmediatelyCommand(Command):
    """
    Command to upload and immediately analyze a meal image.

    This command combines upload and analysis in a single synchronous operation,
    returning complete nutritional analysis without background processing.
    """

    user_id: str
    file_contents: bytes
    content_type: str
    target_date: Optional[date] = None
    user_description: Optional[str] = None  # User-provided context for better accuracy
    language: str = "en"  # ISO 639-1 language code from Accept-Language header
    scan_mode: str = "scanner"
    # When the request arrived: decides whether the log is for its own day,
    # however long the analysis takes.
    received_at: datetime = field(default_factory=utc_now)
