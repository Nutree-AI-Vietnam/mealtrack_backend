import pytest

from src.domain.services.barcode_eval_loop import (
    BarcodeEvalCase,
    BarcodeEvalLoop,
    BarcodeEvalObservation,
)


@pytest.mark.asyncio
async def test_barcode_eval_loop_passes_valid_cases():
    cases = [
        BarcodeEvalCase(
            case_id="case-1",
            barcode="00012345678905",
            scanned_barcode="012345678905",
            aliases=("012345678905", "00012345678905"),
            language="en",
            expected_hit=True,
            expected_source="fatsecret",
            expected_is_estimate=False,
            expected_saveable=True,
            expected_canonical_quarantine=False,
            expected_name="Oatmeal",
            expected_calories_100g_range=(350, 400),
            provider_responses={},
        ),
        BarcodeEvalCase(
            case_id="case-2",
            barcode="bad123",
            scanned_barcode="bad123",
            aliases=("bad123",),
            language="en",
            expected_hit=False,
            expected_source="none",
            expected_is_estimate=False,
            expected_saveable=False,
            expected_canonical_quarantine=True,
            expected_name=None,
            expected_calories_100g_range=None,
            provider_responses={},
        ),
    ]

    async def mock_runner(case: BarcodeEvalCase) -> BarcodeEvalObservation:
        if case.expected_hit:
            return BarcodeEvalObservation(
                response={"name": "Oatmeal"},
                hit=True,
                source="fatsecret",
                is_estimate=False,
                food_reference_id=123,
                name="Oatmeal",
                calories_100g=380.0,
                duration_ms=10.0,
                provider_calls=1,
                is_quarantined_from_canonical=True,
                is_gtin_valid=True,
            )
        return BarcodeEvalObservation(
            response=None,
            hit=False,
            source=None,
            is_estimate=False,
            food_reference_id=None,
            name=None,
            calories_100g=None,
            duration_ms=5.0,
            provider_calls=0,
            is_quarantined_from_canonical=True,
            is_gtin_valid=False,
        )

    eval_loop = BarcodeEvalLoop()
    summary = await eval_loop.evaluate(cases, mock_runner)

    assert summary.case_count == 2
    assert summary.contract_pass_rate == 1.0
    assert summary.gtin_valid_rate == 1.0
    assert summary.source_accuracy_rate == 1.0
    assert summary.saveable_identity_rate == 1.0
    assert summary.quarantine_pass_rate == 1.0
    assert summary.invalid_gtin_accepts == 0
    assert summary.missing_saveable_identity_count == 0
    assert summary.ai_estimate_canonical_eligible_count == 0
    assert summary.catastrophic_outliers == 0

    eval_loop.enforce_gates(summary)


@pytest.mark.asyncio
async def test_barcode_eval_loop_fails_on_unquarantined_estimate():
    cases = [
        BarcodeEvalCase(
            case_id="case-estimate",
            barcode="00099999999999",
            scanned_barcode="099999999999",
            aliases=("099999999999",),
            language="en",
            expected_hit=True,
            expected_source="ai_estimate",
            expected_is_estimate=True,
            expected_saveable=True,
            expected_canonical_quarantine=True,
            expected_name="Snack Bar",
            expected_calories_100g_range=(200, 400),
            provider_responses={},
        )
    ]

    async def mock_runner(case: BarcodeEvalCase) -> BarcodeEvalObservation:
        return BarcodeEvalObservation(
            response={"name": "Snack Bar"},
            hit=True,
            source="ai_estimate",
            is_estimate=True,
            food_reference_id=456,
            name="Snack Bar",
            calories_100g=300.0,
            duration_ms=15.0,
            provider_calls=1,
            is_quarantined_from_canonical=False,  # Leak!
            is_gtin_valid=True,
        )

    eval_loop = BarcodeEvalLoop()
    summary = await eval_loop.evaluate(cases, mock_runner)

    assert summary.ai_estimate_canonical_eligible_count == 1
    assert summary.catastrophic_outliers == 1
    with pytest.raises(AssertionError, match="ai_estimate_canonical_eligible_count"):
        eval_loop.enforce_gates(summary)
