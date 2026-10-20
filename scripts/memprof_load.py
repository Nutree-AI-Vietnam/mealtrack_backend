"""Drive a short staging load and pull three tracemalloc snapshots.

The profiler must already be enabled on the target
(``MEMPROF_ENABLED=true`` and ``ENVIRONMENT`` set to a non-production value
such as ``staging``). This script does not change env vars or deploy.

It refuses to continue when ``GET /health`` reports ``environment=production``
or when that field is missing.

Environment:
  MEMPROF_BASE_URL      API origin, no trailing path (required unless --base-url)
  MEMPROF_ADMIN_TOKEN   Firebase ID token for an ADMIN_EMAILS user (snapshots)
  MEMPROF_USER_TOKEN    Firebase ID token for load traffic (defaults to admin)
  MEMPROF_PLAN_ID       Weekly plan id. Discovered via the current plan when unset.

Example:
  MEMPROF_BASE_URL=https://staging.example.com \\
  MEMPROF_ADMIN_TOKEN="$ADMIN" \\
  MEMPROF_USER_TOKEN="$USER" \\
  python scripts/memprof_load.py --out /tmp/memprof.json

Load between the three snapshots (Accept-Language: en):
  GET /v1/meal-plans/current?auto_generate=false
  GET /v1/meal-plans/{plan_id}/groceries
  GET /v1/foods/search?q=...
  GET /v1/chat
  one POST /v1/chat/messages in the middle window (skip with --skip-chat-post)

Snapshots are per worker pid. The script retries until it sees the same pid
as the first snapshot, because the service runs multiple workers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

FOOD_QUERIES = (
    "chicken breast",
    "brown rice",
    "greek yogurt",
    "banana",
    "salmon",
    "oats",
    "tofu",
    "broccoli",
)
SNAPSHOT_PATH = "/v1/admin/memory/snapshot"
_USER_HEADERS = {"Accept-Language": "en"}


def deployment_environment(health: dict[str, Any]) -> str:
    deployment = health.get("deployment")
    if not isinstance(deployment, dict):
        return ""
    value = deployment.get("environment")
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def ensure_safe_target(health: dict[str, Any]) -> str:
    """Return the reported environment, or raise before any authenticated call."""
    environment = deployment_environment(health)
    if environment == "production":
        raise RuntimeError("refusing to drive load against production")
    if not environment:
        raise RuntimeError(
            "refusing to drive load without a non-production deployment environment"
        )
    return environment


def build_load_calls(
    plan_id: str | None,
    *,
    cycles: int,
    include_chat_post: bool,
) -> list[dict[str, Any]]:
    """Representative EN grocery, food-search, and chat calls. No auto-generate."""
    calls: list[dict[str, Any]] = []
    for index in range(cycles):
        calls.append(
            {
                "method": "GET",
                "path": "/v1/meal-plans/current",
                "params": {
                    "auto_generate": "false",
                    "include_grocery_count": "true",
                },
            }
        )
        if plan_id:
            calls.append(
                {
                    "method": "GET",
                    "path": f"/v1/meal-plans/{plan_id}/groceries",
                }
            )
        calls.append(
            {
                "method": "GET",
                "path": "/v1/foods/search",
                "params": {"q": FOOD_QUERIES[index % len(FOOD_QUERIES)], "limit": "20"},
            }
        )
        calls.append({"method": "GET", "path": "/v1/chat", "params": {"limit": "20"}})
    if include_chat_post:
        calls.append(
            {
                "method": "POST",
                "path": "/v1/chat/messages",
                "json": {
                    "content": "Suggest a high-protein lunch under 600 kcal.",
                    "locale": "en",
                },
                "headers": {"Idempotency-Key": f"memprof-{uuid.uuid4()}"},
            }
        )
    return calls


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("MEMPROF_BASE_URL", ""),
        help="Staging API origin (or MEMPROF_BASE_URL)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=8,
        help="Load cycles in each window between the three snapshots",
    )
    parser.add_argument(
        "--pid-attempts",
        type=int,
        default=16,
        help="How many times to retry a snapshot to land on the first worker pid",
    )
    parser.add_argument(
        "--skip-chat-post",
        action="store_true",
        help="Skip the single chat message POST (GET /v1/chat still runs)",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Write the full JSON report to this path as well as stdout",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Per-request timeout in seconds",
    )
    return parser.parse_args(argv)


def _require_origin(base_url: str) -> str:
    origin = base_url.strip().rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise RuntimeError("MEMPROF_BASE_URL / --base-url must be an http(s) origin")
    if parsed.username or parsed.password:
        raise RuntimeError("base URL must not contain credentials")
    return origin


def _auth_headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", **_USER_HEADERS}
    if extra:
        headers.update(extra)
    return headers


def _request(
    client: httpx.Client,
    call: dict[str, Any],
    *,
    token: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.request(
        call["method"],
        call["path"],
        params=call.get("params"),
        json=call.get("json"),
        headers=_auth_headers(token, call.get("headers")),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "method": call["method"],
        "path": call["path"],
        "status": response.status_code,
        "bytes": len(response.content),
        "elapsed_ms": elapsed_ms,
    }


def _fetch_snapshot(
    client: httpx.Client,
    *,
    admin_token: str,
    want_pid: int | None,
    attempts: int,
) -> dict[str, Any]:
    last_error = ""
    last_body: dict[str, Any] | None = None
    tries = max(1, attempts if want_pid is not None else 1)
    for _ in range(tries):
        response = client.get(
            SNAPSHOT_PATH,
            headers=_auth_headers(admin_token),
        )
        if response.status_code != 200:
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            continue
        body = response.json()
        if not isinstance(body, dict) or "pid" not in body:
            last_error = "snapshot response did not include pid"
            continue
        last_body = body
        if want_pid is None or body.get("pid") == want_pid:
            body["pid_matched"] = True
            return body
    if last_body is not None:
        last_body["pid_matched"] = False
        return last_body
    raise RuntimeError(last_error or "memory snapshot failed")


def _summarize(report: dict[str, Any]) -> str:
    lines = [
        f"environment={report['environment']} base_url={report['base_url']}",
    ]
    for point in report["points"]:
        snap = point["snapshot"]
        diff = snap.get("diff") or {}
        growth = None
        if diff.get("has_previous"):
            growth = sum(row.get("size_diff_bytes", 0) for row in diff.get("top", []))
        lines.append(
            "  {label}: pid={pid} matched={matched} rss={rss} traced={traced} "
            "top={top} diff_top_sum={growth}".format(
                label=point["label"],
                pid=snap.get("pid"),
                matched=snap.get("pid_matched"),
                rss=snap.get("rss_bytes"),
                traced=snap.get("traced_current_bytes"),
                top=len(snap.get("top") or []),
                growth=growth,
            )
        )
    return "\n".join(lines)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cycles < 1:
        print("cycles must be >= 1", file=sys.stderr)
        return 2
    try:
        origin = _require_origin(args.base_url)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    admin_token = os.getenv("MEMPROF_ADMIN_TOKEN", "").strip()
    user_token = os.getenv("MEMPROF_USER_TOKEN", "").strip() or admin_token
    if not admin_token or not user_token:
        print(
            "MEMPROF_ADMIN_TOKEN is required (MEMPROF_USER_TOKEN defaults to it)",
            file=sys.stderr,
        )
        return 2

    plan_id = os.getenv("MEMPROF_PLAN_ID", "").strip() or None
    timeout = httpx.Timeout(args.timeout)
    with httpx.Client(
        base_url=origin, timeout=timeout, follow_redirects=False
    ) as client:
        health_response = client.get("/health")
        if health_response.status_code != 200:
            print(
                f"health check failed: HTTP {health_response.status_code}",
                file=sys.stderr,
            )
            return 2
        try:
            environment = ensure_safe_target(health_response.json())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if plan_id is None:
            current = client.get(
                "/v1/meal-plans/current",
                params={"auto_generate": "false", "include_grocery_count": "false"},
                headers=_auth_headers(user_token),
            )
            if current.status_code == 200:
                payload = current.json()
                found = payload.get("id") if isinstance(payload, dict) else None
                if isinstance(found, str) and found:
                    plan_id = found
            if plan_id is None:
                print(
                    "no weekly plan id; grocery calls skipped "
                    "(set MEMPROF_PLAN_ID or use a user who has a current plan)",
                    file=sys.stderr,
                )

        points: list[dict[str, Any]] = []
        pinned_pid: int | None = None
        for label, include_post in (
            ("before", False),
            ("mid", not args.skip_chat_post),
            ("after", False),
        ):
            snapshot = _fetch_snapshot(
                client,
                admin_token=admin_token,
                want_pid=pinned_pid,
                attempts=args.pid_attempts,
            )
            if pinned_pid is None:
                pinned_pid = int(snapshot["pid"])
            load_results: list[dict[str, Any]] = []
            if label != "after":
                calls = build_load_calls(
                    plan_id,
                    cycles=args.cycles,
                    include_chat_post=include_post,
                )
                for call in calls:
                    load_results.append(_request(client, call, token=user_token))
            points.append(
                {
                    "label": label,
                    "plan_id": plan_id,
                    "load_results": load_results,
                    "snapshot": snapshot,
                }
            )

    report = {
        "base_url": origin,
        "environment": environment,
        "plan_id": plan_id,
        "pinned_pid": pinned_pid,
        "points": points,
    }
    encoded = json.dumps(report, indent=2)
    print(_summarize(report), file=sys.stderr)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
    print(encoded)
    mismatched = [
        point["label"]
        for point in points
        if not point["snapshot"].get("pid_matched", False)
    ]
    if mismatched:
        print(
            "snapshot pid did not match the first worker for: " + ", ".join(mismatched),
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    try:
        code = run()
    except httpx.HTTPError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        code = 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
