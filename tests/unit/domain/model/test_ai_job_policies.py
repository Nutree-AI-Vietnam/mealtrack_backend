"""Retry, error-classification, request-identity, and push-copy rules."""

import hashlib
from datetime import date, timedelta

import pytest
from tests.fixtures.fakes.fake_ai_jobs import NOW, make_error, make_job

from src.domain.model.ai_jobs import (
    AI_JOB_MAX_ATTEMPTS,
    NOTIFIABLE_AI_JOB_STATUSES,
    AiJobError,
    AiJobErrorKind,
    AiJobStatus,
    AiJobType,
)
from src.domain.model.ai_jobs.notification_copy import ai_job_push_copy
from src.domain.model.ai_jobs.policies import (
    PERMANENT_NOT_RETRYABLE_CODES,
    PERMANENT_RETRYABLE_CODES,
    TRANSIENT_CODES,
    ai_job_request_fingerprint,
    can_retry_manually,
    classify_ai_job_error,
    retry_backoff_seconds,
    should_auto_retry,
)

# --- classify_ai_job_error -------------------------------------------------


@pytest.mark.parametrize("code", sorted(PERMANENT_NOT_RETRYABLE_CODES))
def test_codes_that_retrying_cannot_fix_are_permanent_and_final(code):
    assert classify_ai_job_error(code) == AiJobError(
        code=code, kind=AiJobErrorKind.PERMANENT, retryable=False
    )


@pytest.mark.parametrize("code", sorted(PERMANENT_RETRYABLE_CODES))
def test_bad_ai_answers_are_permanent_but_the_user_may_retry(code):
    assert classify_ai_job_error(code) == AiJobError(
        code=code, kind=AiJobErrorKind.PERMANENT, retryable=True
    )


@pytest.mark.parametrize("code", sorted(TRANSIENT_CODES))
def test_infrastructure_codes_are_transient(code):
    assert classify_ai_job_error(code) == AiJobError(
        code=code, kind=AiJobErrorKind.TRANSIENT, retryable=True
    )


def test_unknown_codes_are_treated_as_transient():
    assert classify_ai_job_error("SOMETHING_NEW") == AiJobError(
        code="SOMETHING_NEW", kind=AiJobErrorKind.TRANSIENT, retryable=True
    )


def test_code_sets_do_not_overlap():
    assert not PERMANENT_NOT_RETRYABLE_CODES & PERMANENT_RETRYABLE_CODES
    assert not PERMANENT_NOT_RETRYABLE_CODES & TRANSIENT_CODES
    assert not PERMANENT_RETRYABLE_CODES & TRANSIENT_CODES


# --- should_auto_retry -----------------------------------------------------


@pytest.mark.parametrize(
    ("attempts", "expected"), [(1, True), (2, True), (3, True), (4, False), (5, False)]
)
def test_transient_errors_retry_while_attempts_remain(attempts, expected):
    error = classify_ai_job_error("AI_UNAVAILABLE")

    assert should_auto_retry(error, attempts=attempts, max_attempts=4) is expected


@pytest.mark.parametrize("code", ["AI_OUTPUT_INVALID", "NOT_FOOD_IMAGE"])
def test_permanent_errors_never_retry_automatically(code):
    error = classify_ai_job_error(code)

    assert should_auto_retry(error, attempts=1, max_attempts=4) is False


def test_a_job_gets_at_most_three_automatic_retries():
    error = classify_ai_job_error("AI_TIMEOUT")

    retries = sum(
        should_auto_retry(error, attempts=attempt, max_attempts=AI_JOB_MAX_ATTEMPTS)
        for attempt in range(1, 20)
    )

    assert retries == 3


# --- retry_backoff_seconds -------------------------------------------------


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        (0, 5.0),
        (1, 5.0),
        (2, 10.0),
        (3, 20.0),
        (4, 40.0),
        (10, 2560.0),
        (11, 3600.0),
        (50, 3600.0),
    ],
)
def test_backoff_doubles_from_five_seconds_up_to_an_hour(attempt, expected):
    assert retry_backoff_seconds(attempt, rng=lambda: 0.5) == pytest.approx(expected)


@pytest.mark.parametrize(("rng_value", "expected"), [(0.0, 4.0), (1.0, 6.0)])
def test_backoff_jitter_is_plus_or_minus_twenty_percent(rng_value, expected):
    assert retry_backoff_seconds(1, rng=lambda: rng_value) == pytest.approx(expected)


def test_backoff_caps_the_base_delay_before_jitter():
    assert retry_backoff_seconds(50, rng=lambda: 0.0) == pytest.approx(2880.0)
    assert retry_backoff_seconds(50, rng=lambda: 1.0) == pytest.approx(4320.0)


def test_backoff_uses_random_jitter_by_default():
    delays = [retry_backoff_seconds(2) for _ in range(50)]

    assert all(8.0 <= delay <= 12.0 for delay in delays)


# --- can_retry_manually ----------------------------------------------------


def test_manual_retry_follows_the_error():
    assert can_retry_manually(None) is True
    assert can_retry_manually(make_error(retryable=True)) is True
    assert (
        can_retry_manually(
            make_error("NOT_FOOD_IMAGE", kind=AiJobErrorKind.PERMANENT, retryable=False)
        )
        is False
    )


# --- ai_job_request_fingerprint --------------------------------------------


def test_fingerprint_ignores_key_order_at_every_level():
    first = {"text": "2 eggs", "meta": {"a": 1, "b": [1, 2]}}
    second = {"meta": {"b": [1, 2], "a": 1}, "text": "2 eggs"}

    assert ai_job_request_fingerprint(
        AiJobType.PARSE_TEXT, first
    ) == ai_job_request_fingerprint(AiJobType.PARSE_TEXT, second)


def test_fingerprint_differs_by_type_and_by_input():
    payload = {"text": "2 eggs"}
    base = ai_job_request_fingerprint(AiJobType.PARSE_TEXT, payload)

    assert base != ai_job_request_fingerprint(AiJobType.MEAL_SCAN, payload)
    assert base != ai_job_request_fingerprint(AiJobType.PARSE_TEXT, {"text": "3 eggs"})


def test_fingerprint_hashes_utf8_json_without_escaping():
    canonical = '{"input":{"current_items":null,"text":"phở bò"},"type":"parse_text"}'

    fingerprint = ai_job_request_fingerprint(
        AiJobType.PARSE_TEXT, {"text": "phở bò", "current_items": None}
    )

    assert fingerprint == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert (
        fingerprint
        == "2a398db789c3a01f32f608b888d15a3a5c7b18b785d059696ade44b126a58ade"
    )


def test_fingerprint_hashes_values_without_a_json_form_by_their_string():
    assert ai_job_request_fingerprint(
        AiJobType.MEAL_SCAN, {"target_date": date(2026, 10, 13)}
    ) == ai_job_request_fingerprint(AiJobType.MEAL_SCAN, {"target_date": "2026-10-13"})


# --- ai_job_push_copy ------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "job_type", "title", "body"),
    [
        (
            "en",
            AiJobType.MEAL_SCAN,
            "Your meal is ready",
            "Tap to see the nutrition breakdown.",
        ),
        ("en", AiJobType.PARSE_TEXT, "Your meal is ready", "Tap to review and log it."),
        ("en", AiJobType.CHAT, "Your coach replied", "Tap to read the answer."),
        (
            "vi",
            AiJobType.MEAL_SCAN,
            "Bữa ăn của bạn đã sẵn sàng",
            "Chạm để xem thông tin dinh dưỡng.",
        ),
        (
            "vi",
            AiJobType.PARSE_TEXT,
            "Bữa ăn của bạn đã sẵn sàng",
            "Chạm để xem lại và ghi nhận.",
        ),
        ("vi", AiJobType.CHAT, "Trợ lý đã trả lời", "Chạm để đọc câu trả lời."),
    ],
)
def test_completed_copy(language, job_type, title, body):
    assert ai_job_push_copy(job_type, AiJobStatus.COMPLETED, language=language) == (
        title,
        body,
    )


def test_completed_copy_ignores_retryable():
    assert ai_job_push_copy(
        AiJobType.CHAT, AiJobStatus.COMPLETED, language="en", retryable=False
    ) == ("Your coach replied", "Tap to read the answer.")


@pytest.mark.parametrize(
    ("language", "job_type", "title"),
    [
        ("en", AiJobType.MEAL_SCAN, "We couldn't analyze your meal"),
        ("en", AiJobType.PARSE_TEXT, "We couldn't read your meal"),
        ("en", AiJobType.CHAT, "Your coach couldn't finish replying"),
        ("vi", AiJobType.MEAL_SCAN, "Chưa phân tích được bữa ăn"),
        ("vi", AiJobType.PARSE_TEXT, "Chưa đọc được bữa ăn của bạn"),
        ("vi", AiJobType.CHAT, "Trợ lý chưa trả lời xong"),
    ],
)
def test_failed_titles(language, job_type, title):
    got_title, _ = ai_job_push_copy(job_type, AiJobStatus.FAILED, language=language)

    assert got_title == title


@pytest.mark.parametrize(
    ("language", "retryable", "body"),
    [
        ("en", True, "Tap to try again."),
        ("en", False, "Tap to see what happened."),
        ("vi", True, "Chạm để thử lại."),
        ("vi", False, "Chạm để xem chi tiết."),
    ],
)
def test_failed_body_offers_a_retry_only_when_one_can_help(language, retryable, body):
    for job_type in AiJobType:
        _, got_body = ai_job_push_copy(
            job_type, AiJobStatus.FAILED, language=language, retryable=retryable
        )
        assert got_body == body


def test_failed_copy_offers_a_retry_by_default():
    _, body = ai_job_push_copy(AiJobType.MEAL_SCAN, AiJobStatus.FAILED, language="en")

    assert body == "Tap to try again."


@pytest.mark.parametrize("language", ["vi", "VI", "vi-VN", "Vi-vn"])
def test_vietnamese_copy_matches_any_region_and_case(language):
    assert ai_job_push_copy(
        AiJobType.CHAT, AiJobStatus.COMPLETED, language=language
    ) == ("Trợ lý đã trả lời", "Chạm để đọc câu trả lời.")


@pytest.mark.parametrize("language", ["en-US", "fr", "zh-Hans", ""])
def test_other_languages_fall_back_to_english(language):
    assert ai_job_push_copy(
        AiJobType.CHAT, AiJobStatus.COMPLETED, language=language
    ) == ("Your coach replied", "Tap to read the answer.")


@pytest.mark.parametrize("language", ["en", "vi"])
def test_every_notifiable_job_has_copy(language):
    for job_type in AiJobType:
        for status in sorted(NOTIFIABLE_AI_JOB_STATUSES):
            for retryable in (True, False):
                title, body = ai_job_push_copy(
                    job_type, status, language=language, retryable=retryable
                )
                assert title
                assert body


# --- AiJob -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        (AiJobStatus.QUEUED, False),
        (AiJobStatus.RUNNING, False),
        (AiJobStatus.COMPLETED, True),
        (AiJobStatus.FAILED, True),
        (AiJobStatus.CANCELLED, True),
    ],
)
def test_terminal_statuses(status, terminal):
    assert make_job(status=status).is_terminal is terminal


def test_next_attempt_at_is_set_only_while_an_automatic_retry_waits():
    due = NOW + timedelta(seconds=10)
    error = make_error()

    assert (
        make_job(
            status=AiJobStatus.QUEUED, error=error, available_at=due
        ).next_attempt_at
        == due
    )
    assert make_job(status=AiJobStatus.QUEUED, available_at=due).next_attempt_at is None
    assert (
        make_job(
            status=AiJobStatus.RUNNING, error=error, available_at=due
        ).next_attempt_at
        is None
    )
    assert (
        make_job(
            status=AiJobStatus.FAILED, error=error, available_at=due
        ).next_attempt_at
        is None
    )
