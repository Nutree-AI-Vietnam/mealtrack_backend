"""A chat retry reuses the assistant row it already stored."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.domain.model.chat.models import ChatMessageStatus
from src.domain.services.chat.turn_reuse import (
    existing_reply_disposition,
    replacement_question,
    reuse_assistant_row,
    should_regenerate_on_resume,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_completed_reply_is_replayed():
    assert (
        existing_reply_disposition(
            status=ChatMessageStatus.COMPLETED.value,
            lease_expires_at=None,
            now=NOW,
        )
        == "replay"
    )


def test_live_lease_is_left_in_flight():
    assert (
        existing_reply_disposition(
            status=ChatMessageStatus.GENERATING.value,
            lease_expires_at=NOW + timedelta(seconds=30),
            now=NOW,
        )
        == "busy"
    )


def test_failed_and_expired_replies_are_reused():
    assert (
        existing_reply_disposition(
            status=ChatMessageStatus.FAILED.value,
            lease_expires_at=None,
            now=NOW,
        )
        == "reuse"
    )
    assert (
        existing_reply_disposition(
            status=ChatMessageStatus.GENERATING.value,
            lease_expires_at=NOW - timedelta(seconds=1),
            now=NOW,
        )
        == "reuse"
    )
    assert (
        existing_reply_disposition(
            status=ChatMessageStatus.GENERATING.value,
            lease_expires_at=None,
            now=NOW,
        )
        == "reuse"
    )


def test_missing_reply_creates_the_first_row():
    assert (
        existing_reply_disposition(status=None, lease_expires_at=None, now=NOW)
        == "create"
    )


def test_resume_skips_an_older_reply_and_regenerates_only_the_latest():
    assert should_regenerate_on_resume(disposition="reuse", is_latest=False) is False
    assert should_regenerate_on_resume(disposition="reuse", is_latest=True) is True
    assert should_regenerate_on_resume(disposition="replay", is_latest=False) is False
    assert should_regenerate_on_resume(disposition="busy", is_latest=True) is False


def test_unchanged_question_is_not_rewritten():
    assert replacement_question("Soup", "Soup", limit=100) is None
    assert replacement_question("Soup", "  Soup  ", limit=100) is None
    assert replacement_question("Soup", None, limit=100) is None
    assert replacement_question("Soup", "Stew", limit=100) == "Stew"
    assert replacement_question("Soup", "Stew please", limit=4) == "Stew"
    # Already stored at the limit: a longer resend is the same question.
    assert replacement_question("Stew", "Stew please", limit=4) is None
    assert replacement_question("Stew", "  Stew please  ", limit=4) is None


def test_reuse_keeps_the_row_identity_and_clears_the_old_reply():
    row = SimpleNamespace(
        id="m-asst",
        status=ChatMessageStatus.FAILED.value,
        content="partial",
        reply_payload={"intent": "next_meal"},
        error_code="CHAT_TURN_FAILED",
        completed_at=NOW,
        generation_lease_expires_at=None,
        generation_id="gen-old",
        updated_at=NOW,
    )
    lease = NOW + timedelta(seconds=90)

    reuse_assistant_row(row, lease_expires_at=lease, now=NOW, generation_id="gen-new")

    assert row.id == "m-asst"
    assert row.status == ChatMessageStatus.GENERATING.value
    assert row.content is None
    assert row.reply_payload is None
    assert row.error_code is None
    assert row.completed_at is None
    assert row.generation_lease_expires_at == lease
    assert row.generation_id == "gen-new"
    assert row.updated_at == NOW
