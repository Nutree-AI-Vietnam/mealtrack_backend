"""Settling a buyer's unconfirmed MoMo pass windows against RevenueCat."""

from datetime import UTC, datetime, timedelta

from src.domain.model.payments.momo_pass_chain import PassWindow, recover_chain

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
PASS = timedelta(weeks=4)


def _window(order_id: str, starts_at: datetime) -> PassWindow:
    return PassWindow(order_id, starts_at, starts_at + PASS, PASS)


def _bounds(windows) -> list[tuple[str, datetime, datetime]]:
    return [(window.order_id, window.starts_at, window.ends_at) for window in windows]


def test_window_a_later_confirmed_grant_covers_is_delivered():
    # A's grant failed; B, stacked after A, was granted 30 min later and
    # RevenueCat started it right away, so B's end delivered A as well.
    a = _window("a", NOW - timedelta(hours=3))

    recovery = recover_chain([a], a.ends_at + PASS, [a.ends_at + PASS], NOW)

    assert recovery.delivered == {"a"}
    assert recovery.moved == ()


def test_window_whose_grant_answer_was_lost_is_delivered():
    a = _window("a", NOW - timedelta(hours=3))

    recovery = recover_chain([a], None, [a.ends_at + timedelta(minutes=5)], NOW)

    assert recovery.delivered == {"a"}
    assert recovery.moved == ()


def test_landed_grant_of_a_later_window_delivers_the_chain_before_it():
    a = _window("a", NOW - timedelta(hours=3))
    b = _window("b", a.ends_at)

    recovery = recover_chain([a, b], None, [b.ends_at], NOW)

    assert recovery.delivered == {"a", "b"}


def test_windows_lapsed_in_one_outage_are_stacked_again_from_now_in_order():
    a = _window("a", NOW - timedelta(weeks=1))
    b = _window("b", a.ends_at)  # Not started yet, but stacked on A.

    recovery = recover_chain([a, b], None, [], NOW)

    assert recovery.delivered == frozenset()
    assert _bounds(recovery.moved) == [
        ("a", NOW, NOW + PASS),
        ("b", NOW + PASS, NOW + 2 * PASS),
    ]


def test_new_window_behind_a_lapsed_one_moves_with_it():
    lapsed = _window("lapsed", NOW - timedelta(weeks=5))
    new = _window("new", NOW)

    recovery = recover_chain([lapsed, new], None, [], NOW)

    assert _bounds(recovery.moved) == [
        ("lapsed", NOW, NOW + PASS),
        ("new", NOW + PASS, NOW + 2 * PASS),
    ]


def test_covered_windows_are_settled_and_only_the_rest_move():
    # An old window a confirmed grant behind it covered, and a later one whose
    # grant failed two weeks ago.
    covered = _window("covered", NOW - timedelta(weeks=10))
    lapsed = _window("lapsed", NOW - timedelta(weeks=2))

    recovery = recover_chain(
        [covered, lapsed], NOW - timedelta(weeks=2), [NOW - timedelta(weeks=2)], NOW
    )

    assert recovery.delivered == {"covered"}
    assert _bounds(recovery.moved) == [("lapsed", NOW, NOW + PASS)]


def test_chain_without_a_lapsed_window_keeps_its_end_times():
    started = _window("started", NOW - timedelta(hours=1))
    stacked = _window("stacked", started.ends_at)

    recovery = recover_chain([started, stacked], None, [], NOW)

    assert recovery.delivered == frozenset()
    assert recovery.moved == ()


def test_lapsed_window_stacks_after_any_promotional_upstream():
    lapsed = _window("lapsed", NOW - timedelta(weeks=5))
    # Not one of this buyer's windows, for example a dashboard grant.
    dashboard_end = NOW + timedelta(days=3)

    recovery = recover_chain([lapsed], None, [dashboard_end], NOW)

    assert recovery.delivered == frozenset()
    assert _bounds(recovery.moved) == [("lapsed", dashboard_end, dashboard_end + PASS)]
