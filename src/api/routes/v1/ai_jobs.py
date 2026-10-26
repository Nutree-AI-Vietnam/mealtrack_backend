"""Background AI jobs: enqueue a meal scan or text parse, then observe it.

A worker runs the job; the app polls these routes while it is open and gets
a push for results it has not seen. Chat turns become jobs through
``POST /v1/chat/messages`` instead.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from src.api.dependencies.ai_jobs import get_ai_job_service
from src.api.dependencies.auth import get_current_user_id
from src.api.exceptions import (
    ConflictException,
    ExternalServiceException,
    ResourceNotFoundException,
    ValidationException,
)
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import (
    parse_target_date,
    validate_uploaded_image_url,
)
from src.api.schemas.request.ai_job_requests import (
    AiJobCreateRequest,
    AiJobSeenRequest,
    MealScanJobInput,
    ParseTextJobInput,
)
from src.api.schemas.response.ai_job_responses import (
    AiJobListResponse,
    AiJobResponse,
    AiJobSeenResponse,
)
from src.app.services.ai_jobs import AiJobService
from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobNotFoundError,
    AiJobNotRetryableError,
    AiJobsUnavailableError,
)
from src.domain.model.ai_jobs import (
    AI_JOB_ENQUEUE_RATE_LIMIT,
    AI_JOB_MAX_IDS_PER_LOOKUP,
    ENQUEUEABLE_AI_JOB_TYPES,
    AiJobListState,
    AiJobType,
)
from src.domain.services.prompts.input_sanitizer import sanitize_user_description

router = APIRouter(prefix="/v1/ai-jobs", tags=["AI Jobs"])


@contextmanager
def _job_errors() -> Iterator[None]:
    try:
        yield
    except AiJobNotFoundError as exc:
        raise ResourceNotFoundException(
            message="AI job not found",
            error_code="AI_JOB_NOT_FOUND",
            details={"job_id": exc.job_id},
        ) from exc
    except AiJobNotRetryableError as exc:
        raise ConflictException(
            message="This AI job cannot be retried",
            error_code="AI_JOB_NOT_RETRYABLE",
            details={"job_id": exc.job_id, "status": exc.status},
        ) from exc
    except AiJobConflictError as exc:
        raise ConflictException(
            message="This job id is already used by a different request",
            error_code="AI_JOB_CONFLICT",
            details={"job_id": exc.job_id},
        ) from exc
    except AiJobsUnavailableError as exc:
        raise ExternalServiceException(
            message="Background AI jobs are not available",
            error_code="AI_JOBS_UNAVAILABLE",
            details={"reason": exc.reason},
        ) from exc


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AiJobResponse,
    responses={200: {"model": AiJobResponse, "description": "Job already exists"}},
)
@limiter.limit(AI_JOB_ENQUEUE_RATE_LIMIT)
async def create_ai_job(
    request: Request,
    response: Response,
    payload: AiJobCreateRequest,
    user_id: str = Depends(get_current_user_id),
    service: AiJobService = Depends(get_ai_job_service),
) -> AiJobResponse:
    """Queue a job. Re-sending the same id and input returns the existing job."""
    job_type = _enqueueable_type(payload.type)
    if job_type == AiJobType.MEAL_SCAN:
        job_request = _meal_scan_request(_parse_input(MealScanJobInput, payload.input))
    else:
        job_request = _parse_text_request(
            _parse_input(ParseTextJobInput, payload.input)
        )
    with _job_errors():
        job, created = await service.enqueue(
            job_id=str(payload.id),
            user_id=user_id,
            job_type=job_type,
            language=get_request_language(request),
            request=job_request,
        )
    if not created:
        response.status_code = status.HTTP_200_OK
    return AiJobResponse.from_domain(job)


@router.post("/seen", response_model=AiJobSeenResponse)
async def mark_ai_jobs_seen(
    payload: AiJobSeenRequest,
    user_id: str = Depends(get_current_user_id),
    service: AiJobService = Depends(get_ai_job_service),
) -> AiJobSeenResponse:
    """Record that the app showed these results, so they are not pushed."""
    with _job_errors():
        updated = await service.mark_seen(
            user_id=user_id, job_ids=[str(job_id) for job_id in payload.ids]
        )
    return AiJobSeenResponse(updated=updated)


@router.get("", response_model=AiJobListResponse)
async def list_ai_jobs(
    ids: str | None = Query(
        None,
        description=(
            f"Comma-separated job ids, at most {AI_JOB_MAX_IDS_PER_LOOKUP}; "
            "overrides state"
        ),
    ),
    state: AiJobListState = Query(
        AiJobListState.OPEN,
        description="open: active or unseen; active: queued or running; unseen: finished, not seen",
    ),
    user_id: str = Depends(get_current_user_id),
    service: AiJobService = Depends(get_ai_job_service),
) -> AiJobListResponse:
    """Jobs from the last 24 hours, newest first."""
    with _job_errors():
        if ids is not None:
            jobs = await service.list_by_ids(
                user_id=user_id, job_ids=_parse_job_ids(ids)
            )
        else:
            jobs = await service.list_by_state(user_id=user_id, state=state)
    return AiJobListResponse(jobs=[AiJobResponse.from_domain(job) for job in jobs])


@router.get("/{job_id}", response_model=AiJobResponse)
async def get_ai_job(
    job_id: UUID,
    user_id: str = Depends(get_current_user_id),
    service: AiJobService = Depends(get_ai_job_service),
) -> AiJobResponse:
    with _job_errors():
        job = await service.get(user_id=user_id, job_id=str(job_id))
    return AiJobResponse.from_domain(job)


@router.post("/{job_id}/retry", response_model=AiJobResponse)
@limiter.limit(AI_JOB_ENQUEUE_RATE_LIMIT)
async def retry_ai_job(
    request: Request,
    job_id: UUID,
    user_id: str = Depends(get_current_user_id),
    service: AiJobService = Depends(get_ai_job_service),
) -> AiJobResponse:
    """Run a retryable failed job once more, without new automatic retries."""
    with _job_errors():
        job = await service.retry(user_id=user_id, job_id=str(job_id))
    return AiJobResponse.from_domain(job)


@router.post("/{job_id}/cancel", response_model=AiJobResponse)
async def cancel_ai_job(
    job_id: UUID,
    user_id: str = Depends(get_current_user_id),
    service: AiJobService = Depends(get_ai_job_service),
) -> AiJobResponse:
    """Stop a job. A meal scan that is already running still finishes."""
    with _job_errors():
        job = await service.cancel(user_id=user_id, job_id=str(job_id))
    return AiJobResponse.from_domain(job)


def _enqueueable_type(value: str) -> AiJobType:
    for job_type in ENQUEUEABLE_AI_JOB_TYPES:
        if job_type.value == value:
            return job_type
    raise ValidationException(
        message="Unsupported AI job type",
        error_code="INVALID_JOB_TYPE",
        details={
            "type": value,
            "supported": sorted(t.value for t in ENQUEUEABLE_AI_JOB_TYPES),
        },
    )


def _parse_input[InputT: BaseModel](
    model: type[InputT], data: dict[str, Any]
) -> InputT:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        # Report errors where the client sent the fields.
        raise RequestValidationError(
            [
                {**error, "loc": ("body", "input", *error["loc"])}
                for error in exc.errors(include_url=False)
            ]
        ) from exc


def _meal_scan_request(job_input: MealScanJobInput) -> dict[str, Any]:
    """Validate like the scan-by-url routes; the worker trusts the result."""
    validate_uploaded_image_url(job_input.image_url, job_input.image_id)
    label_crop_image_url: str | None = None
    label_crop_public_id: str | None = None
    crop_metadata: dict[str, Any] | None = None
    user_description = job_input.user_description
    if job_input.scan_mode == "food_label":
        user_description = None
        crop_metadata = job_input.crop_metadata
        has_url = bool(job_input.label_crop_image_url)
        has_id = bool(job_input.label_crop_image_id)
        if has_url != has_id:
            raise ValidationException(
                message="label_crop_image_url and label_crop_image_id must be supplied together",
                error_code="INVALID_LABEL_CROP_IMAGE",
                details={"has_url": has_url, "has_id": has_id},
            )
        if job_input.label_crop_image_url and job_input.label_crop_image_id:
            validate_uploaded_image_url(
                job_input.label_crop_image_url, job_input.label_crop_image_id
            )
            label_crop_image_url = job_input.label_crop_image_url
            label_crop_public_id = f"mealtrack/{job_input.label_crop_image_id}"
    if user_description:
        user_description = sanitize_user_description(user_description) or None
    target_date = parse_target_date(job_input.target_date)
    return {
        "image_url": job_input.image_url,
        "public_id": f"mealtrack/{job_input.image_id}",
        "user_description": user_description,
        "target_date": target_date.isoformat() if target_date else None,
        "scan_mode": job_input.scan_mode,
        "label_crop_image_url": label_crop_image_url,
        "label_crop_public_id": label_crop_public_id,
        "crop_metadata": crop_metadata,
    }


def _parse_text_request(job_input: ParseTextJobInput) -> dict[str, Any]:
    text = sanitize_user_description(job_input.text)
    if not text:
        raise ValidationException(
            message="Invalid or empty meal description.",
            error_code="INVALID_MEAL_TEXT",
        )
    return {"text": text, "current_items": job_input.current_items}


def _parse_job_ids(raw: str) -> list[str]:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if len(values) > AI_JOB_MAX_IDS_PER_LOOKUP:
        raise ValidationException(
            message=f"Ask for at most {AI_JOB_MAX_IDS_PER_LOOKUP} jobs at a time",
            error_code="TOO_MANY_AI_JOB_IDS",
            details={"max": AI_JOB_MAX_IDS_PER_LOOKUP},
        )
    job_ids = []
    for value in values:
        try:
            job_ids.append(str(UUID(value)))
        except ValueError as exc:
            raise ValidationException(
                message="Invalid AI job id",
                error_code="INVALID_AI_JOB_ID",
                details={"id": value[:64]},
            ) from exc
    return job_ids
