"""Opt-in tracemalloc profiler.

Default off. Starts only when ``MEMPROF_ENABLED`` is true and ``ENVIRONMENT``
is set to something other than ``production``. A blank or missing environment
does not count as non-production: the profiler stays off so an unset variable
cannot turn it on. Each worker process keeps its own previous snapshot; the
report includes that process id.
"""

from __future__ import annotations

import os
import threading
import tracemalloc
from datetime import UTC, datetime
from typing import Any

TRACE_FRAMES = 25
TOP_N = 30
_TRUTHY = frozenset({"1", "true", "yes", "on"})

_lock = threading.Lock()
_previous: tracemalloc.Snapshot | None = None
_previous_at: str | None = None
_started_here = False


class MemprofError(Exception):
    """Raised when a snapshot is requested but the profiler must not run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def environment_name() -> str | None:
    """Return the stripped environment, or None when the variable is unset."""
    raw = os.getenv("ENVIRONMENT")
    if raw is None:
        return None
    return raw.strip().lower()


def flag_requested() -> bool:
    return os.getenv("MEMPROF_ENABLED", "").strip().lower() in _TRUTHY


def startup_decision() -> str:
    """Return start, disabled, refuse_production, or refuse_unset_environment."""
    if not flag_requested():
        return "disabled"
    env = environment_name()
    if env == "production":
        return "refuse_production"
    if not env:
        return "refuse_unset_environment"
    return "start"


def is_enabled() -> bool:
    return startup_decision() == "start"


def start_if_enabled() -> bool:
    """Start tracemalloc (25 frames) when the profiler is allowed to run."""
    global _started_here
    if not is_enabled():
        return False
    with _lock:
        if not tracemalloc.is_tracing():
            tracemalloc.start(TRACE_FRAMES)
            _started_here = True
        return True


def stop_if_started() -> None:
    """Stop tracemalloc only if this module started it, and drop the baseline."""
    global _previous, _previous_at, _started_here
    with _lock:
        if _started_here and tracemalloc.is_tracing():
            tracemalloc.stop()
        _started_here = False
        _previous = None
        _previous_at = None


def reset_state_for_tests() -> None:
    """Drop profiler state so unit tests do not leak tracing into the suite."""
    stop_if_started()


def take_report() -> dict[str, Any]:
    """Snapshot the top allocators and the diff since the previous snapshot.

    The previous snapshot is stored in this process only. A later call on
    another worker pid has its own baseline.
    """
    global _previous, _previous_at, _started_here

    decision = startup_decision()
    if decision == "refuse_production":
        raise MemprofError(
            "production",
            "memory profiler is refused in production",
        )
    if decision != "start":
        raise MemprofError("disabled", "memory profiler is disabled")

    with _lock:
        if not tracemalloc.is_tracing():
            tracemalloc.start(TRACE_FRAMES)
            _started_here = True
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        snapshot = _filter_snapshot(tracemalloc.take_snapshot())
        top = _top_statistics(snapshot)
        diff = _diff_against_previous(snapshot)
        _previous = snapshot
        _previous_at = _now()
        return {
            "pid": os.getpid(),
            "enabled": True,
            "nframe": tracemalloc.get_traceback_limit(),
            "group_by": "traceback",
            "traceback_order": "most_recent_first",
            "traced_current_bytes": current_bytes,
            "traced_peak_bytes": peak_bytes,
            "rss_bytes": _current_rss_bytes(),
            "snapshot_at": _previous_at,
            "top": top,
            "diff": diff,
        }


def _filter_snapshot(snapshot: tracemalloc.Snapshot) -> tracemalloc.Snapshot:
    """Hide the profiler's own frames so they do not crowd the top allocators."""
    patterns = ["*/tracemalloc.py"]
    if __file__:
        patterns.append(__file__)
    filters = [tracemalloc.Filter(False, pattern) for pattern in patterns]
    return snapshot.filter_traces(filters)


def _top_statistics(snapshot: tracemalloc.Snapshot) -> list[dict[str, Any]]:
    rows = []
    for rank, stat in enumerate(snapshot.statistics("traceback")[:TOP_N], start=1):
        rows.append(
            {
                "rank": rank,
                "size_bytes": int(stat.size),
                "count": int(stat.count),
                "traceback": _frames(stat.traceback),
            }
        )
    return rows


def _diff_against_previous(snapshot: tracemalloc.Snapshot) -> dict[str, Any]:
    if _previous is None:
        return {
            "has_previous": False,
            "previous_snapshot_at": None,
            "top": [],
        }
    compared = snapshot.compare_to(_previous, "traceback")
    rows = []
    for rank, stat in enumerate(compared[:TOP_N], start=1):
        rows.append(
            {
                "rank": rank,
                "size_bytes": int(stat.size),
                "size_diff_bytes": int(stat.size_diff),
                "count": int(stat.count),
                "count_diff": int(stat.count_diff),
                "traceback": _frames(stat.traceback),
            }
        )
    return {
        "has_previous": True,
        "previous_snapshot_at": _previous_at,
        "top": rows,
    }


def _frames(traceback: tracemalloc.Traceback) -> list[str]:
    return traceback.format(most_recent_first=True)[:TRACE_FRAMES]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _current_rss_bytes() -> int | None:
    """Resident set size from /proc, which is current rather than peak RSS."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            parts = handle.read().split()
        resident_pages = int(parts[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None
    if not isinstance(page_size, int) or page_size <= 0:
        return None
    return resident_pages * page_size
