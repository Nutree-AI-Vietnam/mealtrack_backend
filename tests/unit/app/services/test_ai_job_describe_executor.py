"""Ghi nhanh as a background job: logged like its sync route, replayed by job id."""

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest

from src.api.exceptions import BusinessLogicException
from src.app.commands.describe_any import DescribeAnyCommand
from src.app.commands.hydration import DescribeHydrationCommand
from src.app.commands.movement import DescribeMovementCommand
from src.app.services.ai_jobs.describe_executor import DescribeJobExecutor
from src.app.services.ai_jobs.error_mapping import map_job_exception
from src.app.services.ai_jobs.executors import AiJobInterrupt
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim, AiJobErrorKind, AiJobType
from src.domain.model.ai_jobs.enums import AiJobStatus
from src.domain.model.ai_jobs.notification_copy import ai_job_push_copy
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint

JOB_ID = "6f1c2f4e-3b7a-4c55-9a51-0d9a7f3e2b10"


def _claim(job_type: AiJobType, payload: dict, language: str = "vi") -> AiJobClaim:
    return AiJobClaim(
        id=JOB_ID,
        user_id="u1",
        job_type=job_type,
        language=language,
        request_payload=payload,
        claim_token="t",
        attempts=1,
        max_attempts=4,
        lease_expires_at=datetime(2026, 11, 5, tzinfo=UTC),
    )


def _executor(reply: dict):
    send = AsyncMock(return_value=reply)
    to_result = {job_type: (lambda r: dict(r)) for job_type in AiJobType}
    return DescribeJobExecutor(send=send, to_result=to_result), send


@pytest.mark.asyncio
async def test_movement_logs_with_the_job_id_as_its_replay_key():
    executor, send = _executor({"entries": [{"activity_id": "running"}]})
    payload = {
        "text": "chạy 30 phút",
        "target_date": "2026-11-05",
        "weight_kg": 62,
        "header_timezone": "Asia/Ho_Chi_Minh",
    }

    result = await executor.execute(
        _claim(AiJobType.DESCRIBE_MOVEMENT, payload), AiJobInterrupt()
    )

    assert result == {"entries": [{"activity_id": "running"}]}
    assert send.await_args.args[0] == DescribeMovementCommand(
        user_id="u1",
        text="chạy 30 phút",
        target_date=date(2026, 11, 5),
        weight_kg=62.0,
        header_timezone="Asia/Ho_Chi_Minh",
        idempotency_key=JOB_ID,
        request_fingerprint=canonicalize_fingerprint(
            {"text": "chạy 30 phút", "target_date": "2026-11-05"}
        ),
    )


@pytest.mark.asyncio
async def test_water_keeps_the_jobs_language():
    executor, send = _executor({"entries": [{"drink_id": "tea"}]})

    await executor.execute(
        _claim(
            AiJobType.DESCRIBE_HYDRATION,
            {"text": "2 ly trà", "target_date": None, "header_timezone": None},
            language="en",
        ),
        AiJobInterrupt(),
    )

    command = send.await_args.args[0]
    assert isinstance(command, DescribeHydrationCommand)
    assert (command.language, command.idempotency_key) == ("en", JOB_ID)


@pytest.mark.asyncio
async def test_the_panel_logs_its_kinds_day_and_clock():
    executor, send = _executor({"saved": [{"kind": "food"}], "skipped": []})
    payload = {
        "text": "phở bò",
        "local_date": "2026-11-05",
        "local_time": "07:05",
        "kinds": ["food", "supplement"],
        "header_timezone": None,
    }

    await executor.execute(_claim(AiJobType.DESCRIBE_LOG, payload), AiJobInterrupt())

    command = send.await_args.args[0]
    assert isinstance(command, DescribeAnyCommand)
    assert command.kinds == frozenset({"food", "supplement"})
    assert (command.target_date, command.local_time) == (date(2026, 11, 5), time(7, 5))
    assert command.idempotency_key == JOB_ID
    assert command.request_fingerprint == canonicalize_fingerprint(
        {"text": "phở bò", "local_date": "2026-11-05", "kinds": ["food", "supplement"]}
    )


@pytest.mark.asyncio
async def test_a_panel_sentence_that_saved_nothing_fails_for_good():
    executor, _ = _executor({"saved": [], "skipped": [], "nothing_found": True})
    payload = {"text": "trời đẹp", "kinds": ["food"], "header_timezone": None}

    with pytest.raises(AiJobExecutionError) as exc_info:
        await executor.execute(
            _claim(AiJobType.DESCRIBE_LOG, payload), AiJobInterrupt()
        )

    error = map_job_exception(exc_info.value)
    assert error.code == "DESCRIBE_NOTHING_RECOGNIZED"
    assert (error.kind, error.retryable) == (AiJobErrorKind.PERMANENT, False)


def test_nothing_recognised_by_a_kind_route_is_not_retried():
    error = map_job_exception(
        BusinessLogicException(
            "No activity was recognised in the sentence",
            "DESCRIBE_NOTHING_RECOGNIZED",
        )
    )

    assert (error.kind, error.retryable) == (AiJobErrorKind.PERMANENT, False)


@pytest.mark.parametrize(
    "payload",
    [
        {"kinds": ["food"]},  # no text
        {"text": "phở", "kinds": []},
        {"text": "phở", "kinds": ["dessert"]},
        {"text": "phở", "kinds": ["food"], "local_date": "05/11/2026"},
        {"text": "phở", "kinds": ["food"], "local_time": 705},
    ],
)
@pytest.mark.asyncio
async def test_a_damaged_payload_is_invalid_input(payload):
    executor, send = _executor({"saved": [{"kind": "food"}]})

    with pytest.raises(AiJobExecutionError) as exc_info:
        await executor.execute(
            _claim(AiJobType.DESCRIBE_LOG, payload), AiJobInterrupt()
        )

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    send.assert_not_awaited()


@pytest.mark.parametrize("language", ["en", "vi"])
@pytest.mark.parametrize(
    "job_type",
    [
        AiJobType.DESCRIBE_LOG,
        AiJobType.DESCRIBE_MOVEMENT,
        AiJobType.DESCRIBE_HYDRATION,
    ],
)
@pytest.mark.parametrize("status", [AiJobStatus.COMPLETED, AiJobStatus.FAILED])
def test_every_describe_job_has_push_copy(language, job_type, status):
    title, body = ai_job_push_copy(job_type, status, language=language)

    assert title and body
