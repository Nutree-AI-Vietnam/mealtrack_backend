"""Bounded authenticated planner workflow load on dedicated test fixtures.

Run with --scenario path.json --base-url http://127.0.0.1:8000.
Scenario has accounts (token_env, workflows); each workflow has group and steps
(method, path, optional json/headers, expected_status list). One account per lane;
steps run sequentially, accounts concurrently. Paths and payloads must reference
preseeded dedicated fixtures. Output is aggregate latency/status/bytes only.
"""

import argparse
import asyncio
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlparse

import httpx

GROUPS = {
    "current",
    "generate",
    "plan_patch",
    "ai_proposal",
    "groceries",
    "grocery_patch",
    "slot_log",
    "recipes",
}


def validate(args, scenario):
    host = urlparse(args.base_url).hostname
    if host not in {"localhost", "127.0.0.1", "::1"} and not args.dedicated_test_target:
        raise ValueError(
            "Remote load requires --dedicated-test-target and dedicated accounts"
        )
    if not 1 <= args.users <= 200 or not 1 <= args.seconds <= 3600:
        raise ValueError("Use 1..200 users and 1..3600 seconds")
    accounts = scenario["accounts"]
    if len(accounts) < args.users:
        raise ValueError("Provide one dedicated account per concurrent lane")
    token_keys = [account["token_env"] for account in accounts[: args.users]]
    if len(set(token_keys)) != len(token_keys):
        raise ValueError("Concurrent lanes must use distinct dedicated accounts")
    routes = {
        "current": ("GET", r"/v1/meal-plans/current"),
        "generate": ("POST", r"/v1/meal-plans/generate"),
        "plan_patch": ("PATCH", r"/v1/meal-plans/[^/]+"),
        "ai_proposal": ("POST", r"/v1/meal-plans/[^/]+/ai-prompt"),
        "groceries": ("GET", r"/v1/meal-plans/[^/]+/groceries"),
        "grocery_patch": ("PATCH", r"/v1/meal-plans/[^/]+/groceries(?:/day-lines)?"),
        "slot_log": ("POST", r"/v1/meal-plans/[^/]+/slots/[^/]+/log"),
        "recipes": ("GET", r"/v1/recipes(?:/[^/]+)?"),
    }
    for account in accounts[: args.users]:
        if not os.environ.get(account["token_env"]):
            raise ValueError(
                "A dedicated account token environment variable is missing"
            )
        for workflow in account["workflows"]:
            if workflow["group"] not in GROUPS or not workflow["steps"]:
                raise ValueError("Invalid weekly planner workflow group")
            for step in workflow["steps"]:
                path = step["path"]
                if not path.startswith("/v1/") or "://" in path or ".." in path:
                    raise ValueError("Workflows require relative /v1 paths")
                parsed = urlparse(path)
                method = step.get("method", "GET").upper()
                expected_method, pattern = routes[workflow["group"]]
                if (
                    parsed.fragment
                    or method != expected_method
                    or not re.fullmatch(pattern, parsed.path)
                ):
                    raise ValueError(
                        "Route does not match the declared planner workflow"
                    )
                if {key.lower() for key in step.get("headers", {})} - {
                    "idempotency-key",
                    "x-timezone",
                    "accept-language",
                }:
                    raise ValueError(
                        "Only planner request metadata headers are allowed"
                    )
                if method not in {"GET", "POST", "PATCH"}:
                    raise ValueError("Unsupported workflow method")
                if not args.allow_test_mutations and (
                    method != "GET"
                    or (
                        parsed.path == "/v1/meal-plans/current"
                        and parse_qs(parsed.query).get("auto_generate") != ["false"]
                    )
                ):
                    raise ValueError(
                        "Mutation or legacy auto-generation requires --allow-test-mutations"
                    )
    return accounts[: args.users]


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)


async def run(args, scenario):
    accounts = validate(args, scenario)
    metrics = defaultdict(
        lambda: {"ms": [], "statuses": Counter(), "bytes": 0, "unexpected": 0}
    )
    stop_at = monotonic() + args.seconds

    async def lane(account):
        async with httpx.AsyncClient(
            base_url=args.base_url,
            timeout=35,
            headers={
                "Authorization": f"Bearer {os.environ[account['token_env']]}",
                "Accept-Language": account.get("locale", "en"),
            },
        ) as client:
            while monotonic() < stop_at:
                for workflow in account["workflows"]:
                    if monotonic() >= stop_at:
                        break
                    start, expected, size, status = monotonic(), True, 0, "200"
                    for step in workflow["steps"]:
                        try:
                            response = await client.request(
                                step.get("method", "GET"),
                                step["path"],
                                json=step.get("json"),
                                headers=step.get("headers"),
                            )
                            status = str(response.status_code)
                            size += len(response.content)
                            expected &= response.status_code in step.get(
                                "expected_status", [200]
                            )
                        except httpx.HTTPError:
                            status, expected = "transport_error", False
                            break
                    row = metrics[workflow["group"]]
                    row["ms"].append((monotonic() - start) * 1000)
                    row["statuses"][status] += 1
                    row["bytes"] += size
                    row["unexpected"] += not expected
                    await asyncio.sleep(args.pause_seconds)

    await asyncio.gather(*(lane(account) for account in accounts))
    report = {
        group: {
            "samples": len(row["ms"]),
            "p50_ms": percentile(row["ms"], 0.50),
            "p95_ms": percentile(row["ms"], 0.95),
            "p99_ms": percentile(row["ms"], 0.99),
            "mean_ms": round(statistics.mean(row["ms"]), 3),
            "statuses": dict(row["statuses"]),
            "response_bytes": row["bytes"],
            "unexpected": row["unexpected"],
        }
        for group, row in metrics.items()
    }
    print(json.dumps(report, indent=2))
    return int(any(row["unexpected"] for row in metrics.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--pause-seconds", type=float, default=0.2)
    parser.add_argument("--dedicated-test-target", action="store_true")
    parser.add_argument("--allow-test-mutations", action="store_true")
    args = parser.parse_args()
    if not 0.01 <= args.pause_seconds <= 60:
        parser.error("pause-seconds must be .01..60")
    raise SystemExit(asyncio.run(run(args, json.loads(args.scenario.read_text()))))


if __name__ == "__main__":
    main()
