from src.domain.model.nutrition.micros import Micros
from src.domain.services.nrf_score import nrf_coverage, nrf_quality


def test_quality_maps_full_encourage_and_no_limits_to_100():
    micros = Micros(
        vitamin_a=900,
        vitamin_c=90,
        vitamin_e=15,
        calcium=1300,
        iron=18,
        magnesium=420,
        potassium=4700,
    )
    assert nrf_coverage(micros) == 7
    assert nrf_quality(50, 25, micros) == 100.0


def test_sodium_at_daily_value_pulls_score_to_midpoint():
    micros = Micros(sodium=2300)
    # protein+fiber at DV (100) minus sodium at DV (100) → raw 0 → 50
    assert nrf_quality(50, 25, micros) == 50.0
    assert nrf_coverage(micros) == 1


def test_coverage_ignores_protein_and_fiber():
    assert nrf_coverage(None) == 0
    assert nrf_coverage(Micros()) == 0
    assert nrf_quality(0, 0, None) == 50.0
