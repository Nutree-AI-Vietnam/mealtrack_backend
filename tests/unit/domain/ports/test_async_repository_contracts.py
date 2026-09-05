import inspect

from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.meal_repository_port import MealRepositoryPort
from src.domain.ports.notification_repository_port import NotificationRepositoryPort
from src.domain.ports.saved_suggestion_repository_port import (
    SavedSuggestionRepositoryPort,
)
from src.domain.ports.subscription_repository_port import SubscriptionRepositoryPort
from src.domain.ports.user_repository_port import UserRepositoryPort


def _assert_async_methods(port_cls, method_names):
    sync_methods = [
        name
        for name in method_names
        if not inspect.iscoroutinefunction(getattr(port_cls, name))
    ]
    assert sync_methods == []


def test_repository_ports_are_async_contracts():
    _assert_async_methods(
        MealRepositoryPort,
        [
            "save",
            "find_by_id",
            "find_by_status",
            "find_all_paginated",
            "count",
            "find_by_date",
            "find_by_date_range",
            "delete",
            "get_daily_meal_counts",
        ],
    )
    _assert_async_methods(
        UserRepositoryPort,
        [
            "save",
            "find_by_id",
            "find_by_firebase_uid",
            "find_deleted_by_firebase_uid",
            "find_by_email",
            "find_all",
            "delete",
            "get_profile",
            "update_profile",
            "update_user_timezone",
            "get_user_timezone",
            "update_user_language",
            "get_weekly_auto_adjust",
            "update_user_weekly_auto_adjust",
        ],
    )
    _assert_async_methods(
        SubscriptionRepositoryPort,
        [
            "save",
            "find_by_id",
            "find_by_user_id",
            "find_active_by_user_id",
            "find_expiring_soon",
            "find_expiring_in_window",
            "cancel",
            "reactivate",
            "update_payment_status",
            "extend_trial",
        ],
    )
    _assert_async_methods(
        SavedSuggestionRepositoryPort,
        [
            "find_by_user",
            "find_by_user_and_suggestion",
            "save",
            "delete_by_user_and_suggestion",
            "count_by_user",
        ],
    )
    _assert_async_methods(
        NotificationRepositoryPort,
        [
            "save_notification_preferences",
            "find_notification_preferences_by_user",
            "update_notification_preferences",
            "update_notification_language",
            "delete_notification_preferences",
        ],
    )


def test_async_unit_of_work_contract_declares_runtime_repositories():
    expected_repositories = {
        "session",
        "users",
        "meals",
        "meal_suggestions",
        "subscriptions",
        "notifications",
        "saved_suggestions",
        "saved_suggestions_db",
        "weekly_budgets",
        "cheat_days",
        "hydration_entries",
        "weight_entries",
        "movement_entries",
        "food_references",
        "meal_translations",
    }

    assert expected_repositories <= set(AsyncUnitOfWorkPort.__annotations__)
