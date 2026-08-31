"""Deterministic quality gates and evaluation loop for barcode cascade lookups and estimate quarantine."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any


@dataclass(frozen=True)
class BarcodeEvalCase:
    case_id: str
    barcode: str
    scanned_barcode: str
    aliases: tuple[str, ...]
    language: str
    expected_hit: bool
    expected_source: str
    expected_is_estimate: bool
    expected_saveable: bool
    expected_canonical_quarantine: bool
    expected_name: str | None
    expected_calories_100g_range: tuple[float, float] | None = None
    provider_responses: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BarcodeEvalObservation:
    response: dict[str, Any] | None
    hit: bool
    source: str | None
    is_estimate: bool
    food_reference_id: int | None
    name: str | None
    calories_100g: float | None
    duration_ms: float
    provider_calls: int = 1
    hedged_launched: bool = False
    is_quarantined_from_canonical: bool = True
    is_gtin_valid: bool = True


@dataclass(frozen=True)
class BarcodeEvalCaseResult:
    case_id: str
    contract_pass: bool
    gtin_valid_pass: bool
    source_pass: bool
    saveable_identity_pass: bool
    quarantine_pass: bool
    catastrophic_outlier: bool
    duration_ms: float
    provider_calls: int
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BarcodeEvalSummary:
    case_count: int
    contract_pass_rate: float
    gtin_valid_rate: float
    source_accuracy_rate: float
    saveable_identity_rate: float
    quarantine_pass_rate: float
    invalid_gtin_accepts: int
    missing_saveable_identity_count: int
    ai_estimate_canonical_eligible_count: int
    catastrophic_outliers: int
    provider_calls: tuple[int, ...]
    latency_p50_ms: float
    latency_p95_ms: float
    cases: tuple[BarcodeEvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EvalRunner = Callable[[BarcodeEvalCase], Awaitable[BarcodeEvalObservation]]


class BarcodeEvalLoop:
    """Evaluate barcode lookup cascade and estimate quarantine against deterministic golden cases."""

    async def evaluate(
        self, cases: list[BarcodeEvalCase], runner: EvalRunner
    ) -> BarcodeEvalSummary:
        if not cases:
            raise ValueError("evaluation corpus must not be empty")

        results = [self._evaluate_case(case, await runner(case)) for case in cases]
        latencies = [r.duration_ms for r in results]

        return BarcodeEvalSummary(
            case_count=len(results),
            contract_pass_rate=_rate(results, "contract_pass"),
            gtin_valid_rate=_rate(results, "gtin_valid_pass"),
            source_accuracy_rate=_rate(results, "source_pass"),
            saveable_identity_rate=_rate(results, "saveable_identity_pass"),
            quarantine_pass_rate=_rate(results, "quarantine_pass"),
            invalid_gtin_accepts=sum(not r.gtin_valid_pass for r in results),
            missing_saveable_identity_count=sum(
                not r.saveable_identity_pass for r in results
            ),
            ai_estimate_canonical_eligible_count=sum(
                not r.quarantine_pass for r in results
            ),
            catastrophic_outliers=sum(r.catastrophic_outlier for r in results),
            provider_calls=tuple(r.provider_calls for r in results),
            latency_p50_ms=round(median(latencies), 3),
            latency_p95_ms=round(_percentile(latencies, 0.95), 3),
            cases=tuple(results),
        )

    def _evaluate_case(
        self, case: BarcodeEvalCase, obs: BarcodeEvalObservation
    ) -> BarcodeEvalCaseResult:
        reasons: list[str] = []

        contract_pass = True
        if case.expected_hit:
            if not obs.hit or not obs.response:
                contract_pass = False
                reasons.append("expected_hit_got_miss")
            elif not obs.name:
                contract_pass = False
                reasons.append("missing_product_name")
        else:
            if obs.hit and not obs.is_estimate:
                contract_pass = False
                reasons.append("unexpected_hit_on_invalid_case")

        # GTIN validity: invalid GTINs must not be accepted as hits
        if not obs.is_gtin_valid:
            gtin_valid_pass = not obs.hit
            if not gtin_valid_pass:
                reasons.append("invalid_gtin_accepted_as_valid")
        else:
            gtin_valid_pass = True

        source_pass = True
        if case.expected_hit and case.expected_source:
            source_pass = obs.source == case.expected_source or (
                case.expected_source == "cache"
                and obs.source in {"cache", "fatsecret", "openfoodfacts", "usda_fdc"}
            )
            if not source_pass:
                reasons.append(
                    f"source_mismatch_expected_{case.expected_source}_got_{obs.source}"
                )

        saveable_identity_pass = True
        if case.expected_saveable and case.expected_hit:
            # Must have either durable food_reference_id or valid provider identity
            saveable_identity_pass = bool(obs.food_reference_id or obs.source)
            if not saveable_identity_pass:
                reasons.append("missing_saveable_identity")

        quarantine_pass = True
        if (
            case.expected_canonical_quarantine
            or obs.is_estimate
            or obs.source == "ai_estimate"
        ):
            # Must be quarantined from canonical discovery
            quarantine_pass = obs.is_quarantined_from_canonical
            if not quarantine_pass:
                reasons.append("ai_estimate_leaked_into_canonical_search")

        catastrophic = False
        if not gtin_valid_pass and obs.hit:
            catastrophic = True
            reasons.append("invalid_gtin_accepted_as_valid")
        if case.expected_hit and not saveable_identity_pass:
            catastrophic = True
            reasons.append("successful_lookup_lacking_saveable_identity")
        if not quarantine_pass:
            catastrophic = True
            reasons.append("unquarantined_ai_estimate")

        return BarcodeEvalCaseResult(
            case_id=case.case_id,
            contract_pass=contract_pass,
            gtin_valid_pass=gtin_valid_pass,
            source_pass=source_pass,
            saveable_identity_pass=saveable_identity_pass,
            quarantine_pass=quarantine_pass,
            catastrophic_outlier=catastrophic,
            duration_ms=round(obs.duration_ms, 3),
            provider_calls=obs.provider_calls,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def enforce_gates(summary: BarcodeEvalSummary) -> None:
        failures: list[str] = []
        gates = (
            (summary.contract_pass_rate == 1.0, "contract_pass_rate"),
            (summary.invalid_gtin_accepts == 0, "invalid_gtin_accepts"),
            (
                summary.missing_saveable_identity_count == 0,
                "missing_saveable_identity_count",
            ),
            (
                summary.ai_estimate_canonical_eligible_count == 0,
                "ai_estimate_canonical_eligible_count",
            ),
            (summary.catastrophic_outliers == 0, "catastrophic_outliers"),
            (summary.gtin_valid_rate == 1.0, "gtin_valid_rate"),
            (summary.saveable_identity_rate == 1.0, "saveable_identity_rate"),
            (summary.quarantine_pass_rate == 1.0, "quarantine_pass_rate"),
        )
        for passed, name in gates:
            if not passed:
                failures.append(name)
        if failures:
            raise AssertionError(f"Barcode quality gates failed: {', '.join(failures)}")


def _rate(results: list[BarcodeEvalCaseResult], attr: str) -> float:
    if not results:
        return 0.0
    return round(sum(bool(getattr(r, attr)) for r in results) / len(results), 4)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(math.ceil(pct * len(sorted_vals))) - 1
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]
