"""Queue the one-off "now in US dollars" email for leads who could not buy.

Between 21 and 23 Oct 2026 English visitors saw prices in dong or no plans, so
none could pay. This queues one recovery email (reminder sequence 0) per open,
subscribed lead in a time window. The outbox worker sends it, with a private plan
link and an unsubscribe link; nothing is sent by this script itself.

Old leads carry no locale, so the leads must be named as English explicitly:
pass --lead-ids (one id per line, e.g. exported from Meta or PostHog), or
--assume-english when every lead in the window came from the English funnel.

    python scripts/enqueue_web_funnel_plan_recovery.py --since 2026-10-21 --until 2026-10-24 --lead-ids ids.txt
    python scripts/enqueue_web_funnel_plan_recovery.py ... --execute   # writes; default is a dry run
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from src.app.services.web_funnel_claim_common import utcnow  # noqa: E402
from src.app.services.web_funnel_plan_reminders import (  # noqa: E402
    OPEN_STATUSES,
    REMINDER_JOB,
    reminders_configured,
)
from src.infra.database.config_async import AsyncSessionLocal  # noqa: E402
from src.infra.database.models.web_funnel_claim import (  # noqa: E402
    WebFunnelLead,
    WebFunnelOutbox,
)


def _day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


async def enqueue(
    since: datetime, until: datetime, lead_ids: set[str] | None, execute: bool
) -> int:
    if AsyncSessionLocal is None:
        raise SystemExit("Database is not configured.")
    async with AsyncSessionLocal() as session:
        statement = select(WebFunnelLead).where(
            WebFunnelLead.created_at >= since,
            WebFunnelLead.created_at < until,
            WebFunnelLead.status.in_(OPEN_STATUSES),
            WebFunnelLead.reminders_unsubscribed_at.is_(None),
        )
        if lead_ids is not None:
            statement = statement.where(WebFunnelLead.id.in_(lead_ids))
        leads = (await session.scalars(statement)).all()
        queued = 0
        for lead in leads:
            key = f"{REMINDER_JOB}:0:{lead.id}"
            if await session.scalar(
                select(WebFunnelOutbox.id).where(WebFunnelOutbox.idempotency_key == key)
            ):
                continue
            queued += 1
            print(
                f"{'queue' if execute else 'would queue'} {lead.id} created {lead.created_at:%Y-%m-%d %H:%M}"
            )
            if execute:
                lead.locale = "en"
                session.add(
                    WebFunnelOutbox(
                        idempotency_key=key,
                        job_type=REMINDER_JOB,
                        payload={"lead_id": lead.id, "sequence": 0},
                        status="pending",
                        attempts=0,
                        next_attempt_at=utcnow(),
                    )
                )
        if execute:
            await session.commit()
        return queued


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--since", required=True, help="first day, UTC (YYYY-MM-DD)")
    parser.add_argument(
        "--until", required=True, help="day after the last day, UTC (YYYY-MM-DD)"
    )
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument(
        "--lead-ids", type=Path, help="file with one English lead id per line"
    )
    who.add_argument(
        "--assume-english",
        action="store_true",
        help="every lead in the window is English",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="write the outbox rows (default: dry run)",
    )
    args = parser.parse_args()
    if args.execute and not reminders_configured():
        raise SystemExit(
            "Plan reminders are not configured; the worker would never send these."
        )
    lead_ids = None
    if args.lead_ids:
        lead_ids = {
            line.strip()
            for line in args.lead_ids.read_text().splitlines()
            if line.strip()
        }
    count = asyncio.run(
        enqueue(_day(args.since), _day(args.until), lead_ids, args.execute)
    )
    print(
        f"{count} lead(s) {'queued' if args.execute else 'would be queued (dry run)'}."
    )


if __name__ == "__main__":
    main()
