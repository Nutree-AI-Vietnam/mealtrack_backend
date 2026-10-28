#!/usr/bin/env python3
"""Review opt-in scan corrections and export approved evidence. Never trains a model."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.model.meal.scan_feedback import CONSENT_VERSION
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.meal.scan_feedback import ScanFeedbackORM


def eligible_query(status: str):
    return (
        select(ScanFeedbackORM)
        .join(MealORM, MealORM.meal_id == ScanFeedbackORM.meal_id)
        .where(
            ScanFeedbackORM.consent.is_(True),
            ScanFeedbackORM.status == status,
            ScanFeedbackORM.consent_version == CONSENT_VERSION,
            ScanFeedbackORM.original["image_id"].as_string() == MealORM.image_id,
            ScanFeedbackORM.corrected["image_id"].as_string() == MealORM.image_id,
            ScanFeedbackORM.submission_hash.is_not(None),
            MealORM.status == MealStatusEnum.READY,
        )
        .order_by(ScanFeedbackORM.submitted_at, ScanFeedbackORM.meal_id)
    )


def review(
    session: Session,
    meal_id: str,
    expected_hash: str,
    decision: str,
    reviewer: str,
    note: str,
) -> None:
    if (
        decision not in {"approved", "rejected"}
        or not reviewer.strip()
        or not note.strip()
    ):
        raise ValueError("Decision, reviewer, and a review note are required")
    # Keep lock ordering consistent with submission, withdrawal, and meal deletion.
    meal = session.execute(
        select(MealORM.meal_id).where(MealORM.meal_id == meal_id).with_for_update()
    ).scalar_one_or_none()
    if meal is None:
        raise ValueError("Meal no longer exists")
    row = session.execute(
        eligible_query("pending").where(ScanFeedbackORM.meal_id == meal_id)
    ).scalar_one_or_none()
    if row is None or row.submission_hash != expected_hash:
        raise ValueError(
            "Feedback is withdrawn, already reviewed, or changed; inspect the latest submission"
        )
    row.status = decision
    row.reviewer = reviewer.strip()[:200]
    row.review_note = note.strip()[:1000]
    row.reviewed_at = utc_now()
    session.flush()


def export_records(session: Session, limit: int):
    for row in session.execute(eligible_query("approved").limit(limit)).scalars():
        # No photo URL, user id, or downloaded image is copied into the dataset.
        yield {
            "schema_version": 1,
            "evidence_kind": "reviewed_user_feedback",
            "feedback_id": row.meal_id,
            "submission_hash": row.submission_hash,
            "correction_kind": row.correction_kind,
            "original": row.original,
            "corrected": row.corrected,
            "consent_version": row.consent_version,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }


def validate_records(session: Session, records):
    """Reject exports made stale by withdrawal, deletion, replacement, or re-review."""
    for record in records:
        row = session.execute(
            eligible_query("approved").where(
                ScanFeedbackORM.meal_id == record.get("feedback_id"),
                ScanFeedbackORM.submission_hash == record.get("submission_hash"),
            )
        ).scalar_one_or_none()
        if (
            row is None
            or row.original != record.get("original")
            or row.corrected != record.get("corrected")
        ):
            raise ValueError(
                "Export is stale or modified; generate a fresh approved export before use"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("list")
    inspect.add_argument("--limit", type=int, default=20)
    approve = sub.add_parser("review")
    approve.add_argument("--meal-id", required=True)
    approve.add_argument("--expected-hash", required=True)
    approve.add_argument("--decision", choices=["approved", "rejected"], required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", required=True)
    export = sub.add_parser("export")
    export.add_argument("--output", required=True)
    export.add_argument("--limit", type=int, default=1000)
    validate = sub.add_parser("validate")
    validate.add_argument("--input", required=True)
    args = parser.parse_args()
    if hasattr(args, "limit") and not 1 <= args.limit <= 10000:
        parser.error("limit must be between 1 and 10000")
    from migrations.utils import migration_engine

    with Session(migration_engine) as session:
        if args.command == "list":
            for row in session.execute(
                eligible_query("pending").limit(args.limit)
            ).scalars():
                print(
                    json.dumps(
                        {
                            "meal_id": row.meal_id,
                            "submission_hash": row.submission_hash,
                            "kind": row.correction_kind,
                            "original": row.original,
                            "corrected": row.corrected,
                        },
                        ensure_ascii=False,
                    )
                )
        elif args.command == "review":
            review(
                session,
                args.meal_id,
                args.expected_hash,
                args.decision,
                args.reviewer,
                args.note,
            )
            session.commit()
            print("Review saved")
        elif args.command == "validate":
            with open(args.input) as input_file:
                validate_records(
                    session, (json.loads(line) for line in input_file if line.strip())
                )
            print("Export matches currently approved, consented evidence")
        else:
            # Exclusive creation prevents overwriting an existing evidence artifact.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                for record in export_records(session, args.limit):
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                "Exported approved evidence; recheck consent before every downstream use"
            )


if __name__ == "__main__":
    main()
