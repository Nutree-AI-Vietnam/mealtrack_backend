#!/usr/bin/env python3
"""Reproduce the accuracy baseline report from the QA-labeled fixtures.

Offline, the default:

    python scripts/development/run_accuracy_baseline.py

That command loads the committed 100 scan and 100 prompt labels and runs the
existing offline contract paths. It does not call a provider. ``--grounding``
other than ``not-implemented`` is rejected so the offline report stays stable.

Live, one shared vision pass plus the product parse-text handler. The default
live arm is both: grounding off, then ``FoodReferenceValidationService.ground_meal``
on a copy of that same vision nutrition. The process does not set
``AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED``.

    python scripts/development/run_accuracy_baseline.py --mode live --confirm-live
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

from scripts.development.accuracy_baseline_live import (
    default_live_output,
    render_live_summary,
    require_live_provider_keys,
    run_product_live,
)
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
    "Offline report does not switch AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED. "
    "Live mode calls ground_meal without setting the flag."
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
        "quality_evaluation_status": "contract_only_labeled_photos_not_scored",
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
        "corpus_gate_status": manifest.corpus_gate_status,
        "gold_source_counts": manifest.gold_source_counts,
        "known_micro_case_count": manifest.known_micro_case_count,
        "label_status": manifest.label_status,
        "languages": manifest.languages,
        "macro_case_counts": manifest.macro_case_counts,
        "manifest_sha256": manifest.manifest_sha256,
        "non_food_case_ids": list(manifest.non_food_case_ids),
        "partial_case_ids": list(manifest.partial_case_ids),
        "privacy_reviewed": manifest.privacy_reviewed,
        "quality_evaluation_status": manifest.quality_evaluation_status,
        "quality_flag_counts": manifest.quality_flag_counts,
        "quantity_labeled_items": manifest.quantity_labeled_items,
        "quantity_unlabeled_items": manifest.quantity_unlabeled_items,
        "sample_collection_status": manifest.sample_collection_status,
        "source_provenance": manifest.source_provenance,
        "splits": manifest.splits,
        "surface": manifest.surface,
        "unscored_case_ids": list(manifest.unscored_case_ids),
    }


def _case_id_digest(case_ids: tuple[str, ...]) -> str:
    payload = "\n".join(case_ids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_baseline_report() -> dict[str, Any]:
    """Load the QA-labeled fixtures and attach the offline contract paths.

    Contract success is not photo or text accuracy. Scoring the labels needs a
    live provider run, which this command does not start.
    """
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
                "Not performed. Scan fixtures are the committed EXIF-stripped "
                "JPEGs under tests/fixtures/accuracy_baseline/scan_images/."
            ),
            "loaded_into_fixtures": False,
            "path": POOL_PATH,
            "qa_labeled_fixtures_loaded": True,
            "resampled": False,
            "selection_sql": POOL_SQL,
            "status": "qa_labels_committed_production_ids_not_copied",
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


def _live_arms(args: argparse.Namespace) -> str:
    if args.arm != "default":
        return str(args.arm)
    if args.grounding == "on":
        return "on"
    if args.grounding == "off":
        return "off"
    return "both"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("offline", "live"),
        default="offline",
        help="offline is the contract report. live calls the product providers.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Same as --mode live.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required with live mode. Acknowledges provider spend.",
    )
    parser.add_argument(
        "--arm",
        choices=("default", "off", "on", "both"),
        default="default",
        help=(
            "Live only. default with no --grounding runs off and on. "
            "--grounding off or on selects that arm."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Live provider concurrency. Clamped to 1..4.",
    )
    parser.add_argument(
        "--grounding",
        choices=("not-implemented", "off", "on"),
        default="not-implemented",
        help=(
            "Offline accepts only not-implemented. Live off skips ground_meal. "
            "Live on calls it. The production flag is not changed."
        ),
    )
    parser.add_argument(
        "--output",
        help=(
            "Write the report here. Live mode defaults to a gitignored path. "
            "Omit offline to print the report on stdout."
        ),
    )
    args = parser.parse_args(argv)
    try:
        live = args.mode == "live" or args.live
        if not live and args.grounding != "not-implemented":
            raise RuntimeError(
                "Offline mode does not switch grounding. "
                "Use --mode live --confirm-live. " + _GROUNDING_FOLLOW_UP
            )
        if not live:
            report = build_baseline_report()
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
        if not args.confirm_live:
            raise RuntimeError(
                "live mode requires --confirm-live. No provider call was made."
            )
        if args.concurrency < 1 or args.concurrency > 4:
            raise RuntimeError("concurrency must be from 1 to 4")
        require_live_provider_keys()
        output = args.output or default_live_output()
        report = asyncio.run(
            run_product_live(
                arms=_live_arms(args),
                concurrency=args.concurrency,
                metadata={"git_sha": _git_sha()},
            )
        )
        rendered = render_report(report)
        target, handle = _open_report(output)
        with handle:
            handle.write(rendered)
        os.chmod(target, 0o600)
        summary_path = target.with_suffix(".summary.txt")
        summary_path.write_text(render_live_summary(report), encoding="utf-8")
        os.chmod(summary_path, 0o600)
        sys.stdout.write(render_live_summary(report))
        print(f"mode=accuracy_baseline_live report={target}")
        scored = (
            report["scan"]["grounding_off"]["nutrients"]["kcal"]["scored_cases"] > 0
            and report["text"]["nutrients"]["kcal"]["scored_cases"] > 0
        )
        return 0 if scored else 1
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
