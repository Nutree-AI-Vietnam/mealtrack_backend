from src.infra.services.chat_schema import _chat_schema_is_ready


def test_chat_schema_ready_when_required_columns_exist():
    rows = [
        ("chat_thread", "id"),
        ("chat_thread", "user_id"),
        ("chat_message", "id"),
        ("chat_message", "thread_id"),
        ("chat_message", "role"),
        ("chat_message", "status"),
        ("chat_message", "content"),
        ("chat_message", "idempotency_key"),
        ("chat_message", "citation_source_keys"),
        ("chat_message", "generation_lease_expires_at"),
        ("chat_message", "reply_payload"),
    ]
    assert _chat_schema_is_ready(rows)


def test_chat_schema_not_ready_when_message_table_incomplete():
    rows = [
        ("chat_thread", "id"),
        ("chat_thread", "user_id"),
        ("chat_message", "id"),
    ]
    assert not _chat_schema_is_ready(rows)
