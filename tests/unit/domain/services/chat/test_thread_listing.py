from datetime import UTC, datetime

from src.domain.services.chat.thread_listing import (
    decode_thread_cursor,
    encode_thread_cursor,
    thread_preview,
    thread_title,
)


def test_title_collapses_whitespace_and_clips_on_a_word():
    title = thread_title("  what   should I eat " * 10)
    assert title is not None
    assert title.startswith("what should I eat what")
    assert title.endswith("…")
    assert len(title) <= 80


def test_blank_text_has_no_label():
    assert thread_title("   ") is None
    assert thread_preview(None) is None


def test_preview_drops_markdown_marks_and_keeps_link_text():
    preview = thread_preview("**Grilled fish** — see [the guide](https://x.test) # ok")
    assert preview == "Grilled fish — see the guide ok"


def test_cursor_round_trips_exactly():
    stamp = datetime(2026, 10, 15, 1, 2, 3, 456789, tzinfo=UTC)
    cursor = encode_thread_cursor(stamp, "thread-1")
    assert decode_thread_cursor(cursor) == (stamp, "thread-1")


def test_cursor_rejects_values_it_did_not_issue():
    naive = encode_thread_cursor(datetime(2026, 10, 15), "thread-1")
    for bad in ("thread-1", "!!!", "", naive):
        assert decode_thread_cursor(bad) is None
