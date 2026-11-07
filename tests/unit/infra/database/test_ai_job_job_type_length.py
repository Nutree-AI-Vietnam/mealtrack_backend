"""ai_jobs.job_type must be wide enough for every job type name.

AsyncAiJobRepository.create inserts str(job.job_type) (ai_job_repository.py).
varchar(16) rejected describe_hydration and describe_movement (PYTHON-KZ).
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from src.domain.model.ai_jobs.enums import AiJobType
from src.infra.database.models.ai_job import AiJobORM

_MIGRATION = Path(
    "migrations/versions/20261106175217412906_widen_ai_jobs_job_type_to_varchar_64.py"
)
_OLD_JOB_TYPE_WIDTH = 16


def _job_type_limit() -> int:
    length = AiJobORM.__table__.c.job_type.type.length
    assert isinstance(length, int)
    return length


def _check_constraint_names() -> set[str]:
    check = next(
        constraint
        for constraint in AiJobORM.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_ai_jobs_job_type"
    )
    return set(re.findall(r"'([^']+)'", str(check.sqltext)))


@pytest.mark.parametrize(
    "job_type", list(AiJobType), ids=lambda job_type: job_type.value
)
def test_each_job_type_fits_the_job_type_column(job_type: AiJobType) -> None:
    limit = _job_type_limit()
    stored = str(job_type)

    assert limit == 64
    assert stored == job_type.value
    assert stored in _check_constraint_names()
    assert len(stored) <= limit


def test_every_declared_job_type_is_enumerated() -> None:
    declared = {job_type.value for job_type in AiJobType}

    assert declared == _check_constraint_names()
    assert {
        "chat",
        "meal_scan",
        "parse_text",
        "recipe_import",
        "weekly_plan",
        "describe_log",
        "describe_movement",
        "describe_hydration",
    } <= declared
    # Measured lengths. Both exceed the old varchar(16) and are covered above.
    assert len(str(AiJobType.DESCRIBE_MOVEMENT)) == 17
    assert len(str(AiJobType.DESCRIBE_HYDRATION)) == 18
    assert len(str(AiJobType.DESCRIBE_MOVEMENT)) > _OLD_JOB_TYPE_WIDTH
    assert len(str(AiJobType.DESCRIBE_HYDRATION)) > _OLD_JOB_TYPE_WIDTH


def test_job_type_migration_widens_and_downgrade_does_not_delete_rows() -> None:
    text = _MIGRATION.read_text()

    assert 'revision: str = "20261106175217412906"' in text
    assert 'down_revision: str | None = "20261105103231253233"' in text
    assert "existing_type=sa.String(length=16)" in text
    assert "type_=sa.String(length=64)" in text
    assert "type_=sa.String(length=16)" in text
    assert "DELETE FROM ai_jobs" not in text
    assert "value too long" in text
