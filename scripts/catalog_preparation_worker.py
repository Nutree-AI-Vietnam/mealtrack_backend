"""Run dedicated catalog preparation: python -m scripts.catalog_preparation_worker."""

import argparse
import asyncio
import signal

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.bootstrap.catalog_preparation import (
    build_preparation_computer,
)
from src.infra.workers.catalog_preparation_worker import CatalogPreparationWorker


async def run(args):
    # Separate worker pool: each lane may need its heartbeat while computing.
    from src.infra.workers.catalog_preparation_runtime import create_worker_engine

    engine = create_worker_engine(capacity=args.concurrency + 1)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=args.concurrency,
                max_keepalive_connections=args.concurrency,
            )
        ) as client:
            worker = CatalogPreparationWorker(
                async_sessionmaker(engine, expire_on_commit=False),
                build_preparation_computer(http_client=client),
                concurrency=args.concurrency,
                global_capacity=args.global_capacity,
                lease_seconds=args.lease_seconds,
                provider_deadline_seconds=args.provider_deadline_seconds,
                poll_seconds=args.poll_seconds,
                locales=tuple(args.locales.split(",")),
            )
            if args.once:
                await worker.run_once()
            else:
                await worker.run(stop)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--global-capacity", type=int, default=4)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--provider-deadline-seconds", type=float, default=60)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--locales", default="vi")
    parser.add_argument("--once", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
