"""Per-provider sync health from Open Wearables' sync run summaries.

Open Wearables keeps run summaries for 24 hours. A connection stays
``active`` even when every pull fails (for example Google Health answering
400 ACCOUNT_NOT_LINKED), so the run outcome is the only signal that data is
not arriving.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

SYNC_OK = "ok"
SYNC_SYNCING = "syncing"
SYNC_FAILING = "failing"
SYNC_UNKNOWN = "unknown"

_SUCCEEDED = frozenset({"success", "partial"})


def sync_status(run_status: str) -> str:
    if run_status in _SUCCEEDED:
        return SYNC_OK
    if run_status == "in_progress":
        return SYNC_SYNCING
    if run_status == "failed":
        return SYNC_FAILING
    return SYNC_UNKNOWN


def _when(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def summarize_sync_runs(rows: object) -> dict[str, dict[str, str | None]]:
    """Newest run outcome and last successful run end, keyed by provider."""
    if not isinstance(rows, list):
        return {}
    newest: dict[str, tuple[datetime, str]] = {}
    succeeded: dict[str, tuple[datetime, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = row.get("provider")
        status = row.get("status")
        stamp = _when(row.get("last_update")) or _when(row.get("started_at"))
        if not isinstance(provider, str) or not isinstance(status, str):
            continue
        if stamp is not None:
            current = newest.get(provider)
            if current is None or stamp > current[0]:
                newest[provider] = (stamp, status)
        ended_raw: Any = row.get("ended_at")
        ended = _when(ended_raw)
        if status in _SUCCEEDED and ended is not None:
            best = succeeded.get(provider)
            if best is None or ended > best[0]:
                succeeded[provider] = (ended, ended_raw)
    return {
        provider: {
            "sync_status": sync_status(status),
            "last_synced_at": succeeded[provider][1] if provider in succeeded else None,
        }
        for provider, (_, status) in newest.items()
    }
