"""Tests for cleanup_orphaned_meal_images script."""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch


def _import_cleanup_script():
    """Import the cleanup script by path to avoid module import issues."""
    script_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "scripts",
        "cleanup_orphaned_meal_images.py",
    )
    script_path = os.path.abspath(script_path)
    spec = importlib.util.spec_from_file_location(
        "cleanup_orphaned_meal_images", script_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cleanup_orphaned_meal_images"] = module
    spec.loader.exec_module(module)
    return module


def test_dry_run_does_not_modify_database():
    """Dry-run mode should report orphans but not update database."""
    module = _import_cleanup_script()
    find_orphaned_meals = module.find_orphaned_meals
    mark_meals_failed = module.mark_meals_failed

    # Mock session with candidate meals
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        ("meal-1", "img-1", "Dish 1", None),  # No URL
        ("meal-2", "img-2", "Dish 2", "https://cloudinary.com/img-2.jpg"),  # Has URL
    ]
    mock_session.execute.return_value = mock_result

    # Mock Cloudinary - img-1 not found, img-2 URL returns 404
    mock_cloudinary = MagicMock()
    mock_cloudinary.get_url.return_value = None  # img-1 not found

    with patch("requests.head") as mock_head:
        mock_head.return_value = MagicMock(status_code=404)  # img-2 URL dead

        orphans = find_orphaned_meals(mock_session, mock_cloudinary)

    assert len(orphans) == 2
    assert "meal-1" in orphans
    assert "meal-2" in orphans


def test_mark_meals_failed_updates_status():
    """mark_meals_failed should update meal status to FAILED."""
    module = _import_cleanup_script()
    mark_meals_failed = module.mark_meals_failed

    mock_session = MagicMock()

    orphan_ids = ["meal-1", "meal-2"]
    mark_meals_failed(mock_session, orphan_ids)

    # Verify UPDATE was called with correct params
    mock_session.execute.assert_called_once()
    call_args = mock_session.execute.call_args
    # First arg is the TextClause, second is params dict
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
    assert params == {"id_0": "meal-1", "id_1": "meal-2"}
    mock_session.commit.assert_called_once()
