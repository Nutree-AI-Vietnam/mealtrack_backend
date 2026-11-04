"""Import the curated supplement catalog, or its held-back list, by catalog key.

Usage:
    python scripts/import_supplement_seeds.py scripts/data/supplement_catalog_vn.json.gz
    python scripts/import_supplement_seeds.py scripts/data/supplement_catalog_review.json.gz
    python scripts/import_supplement_seeds.py <file> --dry-run

Records with ``issues`` land in the review queue; the rest are approved.
Re-running changes nothing unless the file did. Rows an admin already
reviewed are left alone. Prints counts as JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bootstrap.supplement_catalog import import_supplement_catalog

_DEFAULT = Path(__file__).resolve().parent / "data" / "supplement_catalog_vn.json.gz"
_SHOWN = 20


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(_DEFAULT))
    parser.add_argument(
        "--dry-run", action="store_true", help="Report counts; write nothing."
    )
    parser.add_argument(
        "--all", action="store_true", help="List every invalid record and skip."
    )
    args = parser.parse_args()

    path = Path(args.path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        print(f"{path} must hold a JSON list of records", file=sys.stderr)
        return 2

    summary = asyncio.run(import_supplement_catalog(records, dry_run=args.dry_run))
    shown = None if args.all else _SHOWN
    print(
        json.dumps(
            {
                "file": path.name,
                "applied": not args.dry_run,
                "received": summary.received,
                "held_back": summary.held_back,
                "inserted": summary.inserted,
                "updated": summary.updated,
                "unchanged": summary.unchanged,
                "kept_reviewed": summary.kept_reviewed,
                "kept_approved": summary.kept_approved,
                "invalid": len(summary.invalid),
                "barcode_skips": len(summary.barcode_skips),
                "invalid_records": [
                    {"record": i.record, "error": i.error}
                    for i in summary.invalid[:shown]
                ],
                "kept_approved_keys": summary.kept_approved_keys[:shown],
                "skipped_barcodes": [
                    {"key": s.catalog_key, "barcode": s.barcode, "reason": s.reason}
                    for s in summary.barcode_skips[:shown]
                ],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
