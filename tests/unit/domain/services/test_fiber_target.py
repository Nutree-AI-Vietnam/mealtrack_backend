from src.domain.services.fiber_target import fiber_daily_target_g


def test_fiber_target_uses_14g_per_1000_kcal_with_25g_floor():
    assert fiber_daily_target_g(2000) == 28.0
    assert fiber_daily_target_g(1500) == 25.0
    assert fiber_daily_target_g(0) == 25.0
