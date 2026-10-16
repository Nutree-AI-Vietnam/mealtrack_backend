"""Run the entitlement ledger worker: python -m scripts.entitlement_worker.

Idle unless the ``ent_reconcile`` feature flag is on. ``--once`` runs a single
pass including the timed jobs and prints its counts as JSON.
"""

import argparse
import asyncio
import json
import logging
import signal

from src.bootstrap.entitlement_worker import build_entitlement_worker


async def run(args: argparse.Namespace) -> dict | None:
    worker = build_entitlement_worker(
        batch_size=args.batch_size,
        periodic_batch_size=args.periodic_batch_size,
        lease_seconds=args.lease_seconds,
        poll_seconds=args.poll_seconds,
        periodic_interval_seconds=args.periodic_interval_seconds,
        sweep_interval_seconds=args.sweep_interval_seconds,
        resync_after_hours=args.resync_after_hours,
    )
    if args.once:
        return (await worker.run_once(force_periodic=True)).as_dict()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await worker.run(stop)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--periodic-batch-size", type=int, default=50)
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--periodic-interval-seconds", type=float, default=60.0)
    parser.add_argument("--sweep-interval-seconds", type=float, default=900.0)
    parser.add_argument("--resync-after-hours", type=float, default=24.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    summary = asyncio.run(run(build_parser().parse_args(argv)))
    if summary is not None:
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
