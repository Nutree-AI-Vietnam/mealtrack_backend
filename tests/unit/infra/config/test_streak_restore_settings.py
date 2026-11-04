from datetime import date
from unittest.mock import patch

import pytest

from src.api.base_dependencies import get_streak_restore_policy
from src.infra.config.settings import Settings


def test_same_day_rule_is_off_when_unset_or_blank():
    assert Settings(_env_file=None).STREAK_SAME_DAY_LOGS_FROM is None
    blank = Settings(STREAK_SAME_DAY_LOGS_FROM="", _env_file=None)
    assert blank.STREAK_SAME_DAY_LOGS_FROM is None


def test_restore_spend_and_claim_default_off():
    settings = Settings(_env_file=None)
    assert settings.STREAK_RESTORE_SPEND_ENABLED is False
    assert settings.STREAK_RESTORE_CLAIM_ENABLED is False


@pytest.fixture
def policy_from():
    def build(**values):
        get_streak_restore_policy.cache_clear()
        with patch(
            "src.infra.config.settings.get_settings",
            return_value=Settings(_env_file=None, **values),
        ):
            return get_streak_restore_policy()

    yield build
    get_streak_restore_policy.cache_clear()


def test_policy_defaults_spend_and_claim_off(policy_from):
    policy = policy_from()
    assert policy.spend_enabled is False
    assert policy.claim_enabled is False


def test_policy_reads_switches_and_the_sandbox_allow_list(policy_from):
    policy = policy_from(
        ENVIRONMENT="production",
        STREAK_RESTORE_SANDBOX_USER_IDS=" review-user, qa-user ,,",
        STREAK_RESTORE_SPEND_ENABLED=False,
        STREAK_RESTORE_CLAIM_ENABLED=True,
        STREAK_SAME_DAY_LOGS_FROM="2026-11-10",
    )
    assert policy.production is True
    assert policy.sandbox_user_ids == frozenset({"review-user", "qa-user"})
    assert policy.spend_enabled is False
    assert policy.claim_enabled is True
    assert policy.same_day_logs_from == date(2026, 11, 10)


def test_staging_credits_every_sandbox_purchase(policy_from):
    policy = policy_from(ENVIRONMENT="staging")
    assert policy.allows_sandbox("anyone")
