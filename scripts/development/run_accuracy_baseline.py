#!/usr/bin/env python3
"""Reproduce the accuracy baseline report from the existing eval paths.

One command:

    python scripts/development/run_accuracy_baseline.py

The report is contract evidence plus the synthetic 100+100 scaffolds. It does
not call a provider, does not read the candidate pool, and does not switch
reference grounding. ``--grounding on|off`` is item #2
(``AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED``) and is rejected here.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.development.accuracy_baseline_scaffold import (
    POOL_PATH,
    POOL_SQL,
    QUALITY_STATUS,
)
from scripts.development.evaluate_meal_analyze_prompt_candidates import (
    _contract_cases,
)
from scripts.development.evaluate_parse_text_nutrition import (
    load_corpus,
    run_offline,
)
from scripts.development.meal_scan_eval_manifest import (
    load_scan_scaffold_manifest,
)
from scripts.development.parse_text_eval_manifest import (
    load_text_scaffold_manifest,
)

from src.domain.services.meal_analysis.prompt_eval_loop import PromptEvalLoop

# Same cap as evaluate_parse_text_nutrition.py --mode offline.
_OFFLINE_TEXT_CAP = 25
_GROUNDING_FOLLOW_UP = (
    "Item #2 AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED. "
    "This harness does not switch grounding."
)


def _git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _scan_contract() -> dict[str, Any]:
    prompt = "contract-only: prompt quality is not measured"
    result = PromptEvalLoop().rank_candidates(
        {"contract_fixture": prompt}, _contract_cases()
    )[0]
    return {
        "candidate_quality_measured": False,
        "case_count": result.case_count,
        "existing_eval_path": (
            "python scripts/development/evaluate_meal_analyze_prompt_candidates.py "
            "--mode contract"
        ),
        "mode": "offline_contract_only",
        "parse_success_rate": result.parse_success_rate,
        "prompt_accuracy": None,
        "quality_evaluation_status": "blocked_no_reviewed_photo_corpus",
        "validation_success_rate": result.validation_success_rate,
    }


def _stable_text_contract(summary: Any, drop_case_count: int) -> dict[str, Any]:
    """Keep fixture pass/fail fields. Drop machine-local timings."""
    return {
        "candidate_pass_rate": summary.candidate_pass_rate,
        "candidate_quality_measured": False,
        "case_count": summary.case_count,
        "cases": [
            {
                "candidate_pass": result.candidate_pass,
                "case_id": result.case_id,
                "contract_pass": result.contract_pass,
                "identity_pass": result.identity_pass,
                "quantity_pass": result.quantity_pass,
                "reference_pass": result.reference_pass,
            }
            for result in summary.cases
        ],
        "catastrophic_outliers": summary.catastrophic_outliers,
        "common_reference_pass_rate": summary.common_reference_pass_rate,
        "contract_pass_rate": summary.contract_pass_rate,
        "drop_case_count": drop_case_count,
        "evidence_kind": summary.evidence_kind,
        "existing_eval_path": (
            "python scripts/development/evaluate_parse_text_nutrition.py --mode offline"
        ),
        "identity_quantity_pass_rate": summary.identity_quantity_pass_rate,
        "invalid_reference_accepts": summary.invalid_reference_accepts,
        "mode": "offline_contract_only",
        "provider_calls": list(summary.provider_calls),
        "quality_metrics": None,
    }


def _scaffold_section(manifest: Any) -> dict[str, Any]:
    return {
        "case_count": manifest.case_count,
        "case_ids_sha256": _case_id_digest(manifest.case_ids),
        "label_status": manifest.label_status,
        "languages": manifest.languages,
        "manifest_sha256": manifest.manifest_sha256,
        "privacy_reviewed": manifest.privacy_reviewed,
        "quality_evaluation_status": manifest.quality_evaluation_status,
        "sample_collection_status": manifest.sample_collection_status,
        "source_provenance": manifest.source_provenance,
        "splits": manifest.splits,
        "surface": manifest.surface,
    }


def _case_id_digest(case_ids: tuple[str, ...]) -> str:
    payload = "\n".join(case_ids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_baseline_report() -> dict[str, Any]:
    """Run the existing offline eval paths and attach the synthetic scaffolds."""
    scan_scaffold = load_scan_scaffold_manifest()
    text_scaffold = load_text_scaffold_manifest()
    reference_cases, drop_cases = load_corpus()
    reference_cases = reference_cases[:_OFFLINE_TEXT_CAP]
    drop_cases = drop_cases[:_OFFLINE_TEXT_CAP]
    text_summary = asyncio.run(run_offline(reference_cases, drop_cases))
    return {
        "accuracy_claim": None,
        "candidate_pool": {
            "image_fetch": (
                "Backend is EXIF-stripping scan images in the restricted store. "
                "This report does not download images."
            ),
            "loaded_into_fixtures": False,
            "path": POOL_PATH,
            "resampled": False,
            "selection_sql": POOL_SQL,
            "status": "privacy_review_before_trim_to_100",
            "trim_target": {"parse_text": 100, "scan": 100},
        },
        "candidate_quality_measured": False,
        "git_sha": _git_sha(),
        "grounding_arm": {
            "follow_up": _GROUNDING_FOLLOW_UP,
            "implemented": False,
            "requested": "not-implemented",
        },
        "provider_calls": 0,
        "quality_evaluation_status": QUALITY_STATUS,
        "report_kind": "accuracy_baseline",
        "scan": {
            "contract": _scan_contract(),
            "scaffold": _scaffold_section(scan_scaffold),
        },
        "schema_version": 1,
        "text": {
            "offline_contract": _stable_text_contract(text_summary, len(drop_cases)),
            "scaffold": _scaffold_section(text_scaffold),
        },
    }


def render_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _open_report(path: str):
    target = Path(path).expanduser().resolve()
    if target.exists():
        raise RuntimeError("refusing to overwrite an existing evaluation report")
    if target.is_relative_to(PROJECT_ROOT):
        checked = subprocess.run(
            ["git", "check-ignore", "--no-index", str(target)],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if checked.returncode != 0:
            raise RuntimeError("repository report paths must already be git-ignored")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    return target, os.fdopen(fd, "w", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grounding",
        choices=("not-implemented",),
        default="not-implemented",
        help=("Reserved slot. on|off grounding is item #2 and is not accepted."),
    )
    parser.add_argument(
        "--output",
        help="Write the report here. Omit to print the report on stdout.",
    )
    args = parser.parse_args(argv)
    try:
        report = build_baseline_report()
        if args.grounding != "not-implemented":
            raise RuntimeError(_GROUNDING_FOLLOW_UP)
        rendered = render_report(report)
        if args.output:
            target, handle = _open_report(args.output)
            with handle:
                handle.write(rendered)
            os.chmod(target, 0o600)
            print(
                "mode=accuracy_baseline "
                f"quality={report['quality_evaluation_status']} report={target}"
            )
        else:
            sys.stdout.write(rendered)
        scan_ok = (
            report["scan"]["contract"]["parse_success_rate"] == 1.0
            and report["scan"]["contract"]["validation_success_rate"] == 1.0
        )
        text_ok = report["text"]["offline_contract"]["contract_pass_rate"] == 1.0
        return 0 if scan_ok and text_ok else 1
    except (
        RuntimeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"accuracy baseline failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
