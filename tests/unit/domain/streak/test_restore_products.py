"""One pack-id rule; the app tests the same vectors."""

import pytest

from src.domain.model.streak import (
    StreakRestorePolicy,
    mentions_streak_restore,
    restore_count_for,
)


@pytest.mark.parametrize(
    ("product_id", "count"),
    [
        ("nutree_streak_restore_1", 1),
        ("nutree_streak_restore_3", 3),
        ("nutree_streak_restore_7", 7),
        ("staging_nutree_streak_restore_3", 3),
        ("streak_restore_7", 7),
        ("nutree_streak_restore_3:buy", 3),
        ("nutree_streak_restore", None),
        ("nutree_streak_restore_0", None),
        ("nutree_streak_restore_x", None),
        ("nutree_streak_restore_3_promo", None),
        ("599yearly_99weekly", None),
        ("nutree_premium_monthly:monthly", None),
        ("", None),
        (None, None),
    ],
)
def test_restore_count_for(product_id, count):
    assert restore_count_for(product_id) == count


@pytest.mark.parametrize(
    ("product_id", "mentioned"),
    [
        ("nutree_streak_restore_3_promo", True),
        ("nutree_streak_restore", True),
        ("NUTREE_STREAK_RESTORE_3", True),
        ("599yearly_99weekly", False),
        (None, False),
    ],
)
def test_mentions_streak_restore_catches_uncountable_packs(product_id, mentioned):
    assert mentions_streak_restore(product_id) is mentioned


def test_sandbox_purchases_credit_everyone_outside_production():
    assert StreakRestorePolicy(production=False).allows_sandbox("anyone")


def test_sandbox_purchases_credit_only_listed_users_in_production():
    policy = StreakRestorePolicy(production=True, sandbox_user_ids=frozenset({"qa"}))
    assert policy.allows_sandbox("qa")
    assert not policy.allows_sandbox("customer")
