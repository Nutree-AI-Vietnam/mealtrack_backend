#!/usr/bin/env python3
"""Contract-check scan output or compare explicit prompts on reviewed photos.

Offline mode is schema/parser evidence only. Provider mode requires a reviewed,
privacy-approved photo manifest and explicit prompt snapshots; it never writes
meals or performs reference lookups.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.development.meal_scan_eval_manifest import (
    _corpus_status,
    load_reviewed_manifest,
)
from scripts.development.meal_scan_eval_prompt_bundle import (
    MAX_PROVIDER_CASES,
    _contract_cases,
    load_prompt_bundle,
    prompt_for_language,
    rendered_prompt_hashes,
    resolve_gate_candidate,
)
from scripts.development.meal_scan_eval_provider import (
    run_provider_pair,
    select_provider_batch,
)

from src.domain.services.meal_analysis.prompt_eval_loop import PromptEvalLoop


def _open_report(path: str | None):
    if path is None:
        fd, raw_path = tempfile.mkstemp(prefix="meal-scan-eval-", suffix=".json")
        return Path(raw_path), os.fdopen(fd, "w", encoding="utf-8")
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


def write_report(report: dict[str, Any], path: str | None) -> Path:
    target, handle = _open_report(path)
    with handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(target, 0o600)
    return target


def assert_provider_staging_allowed(confirm_provider_evaluation: bool) -> None:
    if os.getenv("ENVIRONMENT") != "staging":
        raise RuntimeError(
            "provider evaluation requires authoritative ENVIRONMENT=staging"
        )
    if not confirm_provider_evaluation:
        raise RuntimeError("provider evaluation requires --confirm-provider-evaluation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("contract", "provider"), default="contract")
    parser.add_argument("--manifest")
    parser.add_argument("--baseline-prompt")
    parser.add_argument("--candidate-prompt")
    parser.add_argument("--max-cases", type=int, default=25)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--split", choices=("development", "held_out"), default="development"
    )
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--confirm-provider-evaluation", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.mode == "contract":
            prompt = "contract-only: prompt quality is not measured"
            result = PromptEvalLoop().rank_candidates(
                {"contract_fixture": prompt}, _contract_cases()
            )[0]
            report = {
                "mode": "offline_contract_only",
                "candidate_quality_measured": False,
                "quality_evaluation_status": "blocked_no_reviewed_photo_corpus",
                "case_count": result.case_count,
                "parse_success_rate": result.parse_success_rate,
                "validation_success_rate": result.validation_success_rate,
                "prompt_accuracy": None,
            }
            path = write_report(report, args.output)
            print(
                f"mode=offline_contract_only candidate_quality=not_measured report={path}"
            )
            return (
                0
                if result.parse_success_rate == 1.0
                and result.validation_success_rate == 1.0
                else 1
            )
        if not args.manifest or not args.baseline_prompt or not args.candidate_prompt:
            raise ValueError(
                "provider mode requires --manifest, --baseline-prompt, and --candidate-prompt"
            )
        assert_provider_staging_allowed(args.confirm_provider_evaluation)
        if args.case_offset < 0:
            raise ValueError("--case-offset must be non-negative")
        baseline, baseline_hash = load_prompt_bundle(
            args.baseline_prompt, family="meal_scan"
        )
        candidate, candidate_hash = load_prompt_bundle(
            args.candidate_prompt, family="meal_scan"
        )
        if baseline == candidate:
            raise ValueError("baseline and candidate prompt bundles are identical")
        reviewed_cases = load_reviewed_manifest(args.manifest)
        evaluated_cases = [case for case in reviewed_cases if case.split == args.split]
        # Every language represented in the corpus must resolve to a frozen prompt.
        for case in reviewed_cases:
            prompt_for_language(baseline, case.language)
            prompt_for_language(candidate, case.language)
        report = asyncio.run(
            run_provider_pair(
                evaluated_cases,
                baseline,
                candidate,
                args.repetitions,
                max(1, min(args.max_cases, MAX_PROVIDER_CASES)),
                case_offset=args.case_offset,
            )
        )
        report["prompt_hashes"] = {
            "baseline": baseline_hash,
            "candidate": candidate_hash,
        }
        languages = {case.language for case in reviewed_cases}
        report["rendered_prompt_hashes"] = {
            "baseline": rendered_prompt_hashes(baseline, languages),
            "candidate": rendered_prompt_hashes(candidate, languages),
        }
        report["quality_evaluation_status"] = _corpus_status(reviewed_cases)
        report["evaluated_split"] = args.split
        report["corpus_version"] = json.loads(
            Path(args.manifest).read_text(encoding="utf-8")
        ).get("corpus_version")
        path = write_report(report, args.output)
        print(
            f"mode=provider cases={report['case_count']} generations={report['provider_generations']} report={path}"
        )
        return 0
    except (
        RuntimeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        TimeoutError,
    ) as exc:
        print(
            f"meal scan evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "main",
    "resolve_gate_candidate",
    "run_provider_pair",
    "select_provider_batch",
]
