import argparse
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic.util.exc import CommandError
from migrations.cli import (
    cmd_check_downgrades,
    cmd_downgrade,
    cmd_generate,
    cmd_rollback,
    cmd_status,
    cmd_upgrade,
)
from migrations.run import run_migrations
from migrations.utils import (
    generate_sequential_revision_id,
    generate_timestamp_revision_id,
)


def test_generate_timestamp_revision_id_formats_yyyymmddhhmmss() -> None:
    target_time = datetime(2026, 8, 29, 0, 0, 2, tzinfo=UTC)
    rev_id = generate_timestamp_revision_id(target_time=target_time)
    assert rev_id == "20260829000002"
    assert len(rev_id) == 14


def test_generate_timestamp_revision_id_avoids_same_second_collision(
    tmp_path: Path,
) -> None:
    target_time = datetime(2026, 8, 29, 0, 0, 2, tzinfo=UTC)
    (tmp_path / "20260829000002_first.py").write_text("# rev", encoding="utf-8")

    rev_id = generate_timestamp_revision_id(tmp_path, target_time=target_time)
    assert rev_id == "20260829000003"
    assert len(rev_id) == 14


def test_generate_sequential_revision_id_alias() -> None:
    target_time = datetime(2026, 8, 29, 0, 0, 2, tzinfo=UTC)
    rev_id = generate_sequential_revision_id(target_time=target_time)
    assert rev_id == "20260829000002"


def test_cmd_generate_rejects_empty_message() -> None:
    args = argparse.Namespace(message="   ")
    assert cmd_generate(args) == 1


def test_cmd_generate_calls_alembic_revision() -> None:
    args = argparse.Namespace(message="add new table", empty=False)
    with (
        patch("migrations.cli.get_alembic_config") as mock_cfg,
        patch("migrations.cli.command.revision") as mock_rev,
    ):
        mock_cfg.return_value = MagicMock()
        ret = cmd_generate(args)
        assert ret == 0
        mock_rev.assert_called_once_with(
            mock_cfg.return_value,
            message="add new table",
            autogenerate=True,
        )


def test_cmd_generate_empty_disables_autogenerate() -> None:
    args = argparse.Namespace(message="add custom ddl", empty=True)
    with (
        patch("migrations.cli.get_alembic_config") as mock_cfg,
        patch("migrations.cli.command.revision") as mock_rev,
    ):
        mock_cfg.return_value = MagicMock()
        ret = cmd_generate(args)
        assert ret == 0
        mock_rev.assert_called_once_with(
            mock_cfg.return_value,
            message="add custom ddl",
            autogenerate=False,
        )


def test_cmd_upgrade_default_target_is_head() -> None:
    args = argparse.Namespace(target="head")
    with (
        patch("migrations.cli.get_alembic_config"),
        patch("migrations.cli.command.upgrade") as mock_upgrade,
        patch("migrations.cli.MigrationContext") as mock_ctx,
    ):
        mock_ctx.configure.return_value.get_current_revision.side_effect = [
            "20260829000001",
            "20260829000002",
        ]
        with patch("migrations.utils.migration_engine.connect"):
            ret = cmd_upgrade(args)
            assert ret == 0
            mock_upgrade.assert_called_once()


def test_cmd_downgrade_with_steps_or_target() -> None:
    args = argparse.Namespace(steps=2, target=None)
    with (
        patch("migrations.cli.get_alembic_config") as mock_cfg,
        patch("migrations.cli.command.downgrade") as mock_down,
        patch("migrations.cli.MigrationContext") as mock_ctx,
    ):
        mock_ctx.configure.return_value.get_current_revision.side_effect = [
            "20260829000003",
            "20260829000001",
        ]
        with patch("migrations.utils.migration_engine.connect"):
            ret = cmd_downgrade(args)
            assert ret == 0
            mock_down.assert_called_once_with(mock_cfg.return_value, "-2")


def test_cmd_rollback_validates_target_revision() -> None:
    args = argparse.Namespace(target_revision="20260829000001")
    with (
        patch("migrations.cli.get_alembic_config") as mock_cfg,
        patch("migrations.cli.ScriptDirectory.from_config") as mock_sd,
        patch("migrations.cli.command.downgrade") as mock_down,
        patch("migrations.cli.MigrationContext") as mock_ctx,
    ):
        mock_sd.return_value.get_revision.return_value = MagicMock()
        mock_ctx.configure.return_value.get_current_revision.side_effect = [
            "20260829000003",
            "20260829000001",
        ]
        with patch("migrations.utils.migration_engine.connect"):
            ret = cmd_rollback(args)
            assert ret == 0
            mock_down.assert_called_once_with(mock_cfg.return_value, "20260829000001")


def test_cmd_rollback_rejects_unknown_revision() -> None:
    args = argparse.Namespace(target_revision="unknown_revision_id")
    with (
        patch("migrations.cli.get_alembic_config"),
        patch("migrations.cli.ScriptDirectory.from_config") as mock_sd,
    ):
        mock_sd.return_value.get_revision.side_effect = CommandError("Not found")
        ret = cmd_rollback(args)
        assert ret == 1


def test_cmd_status_detects_orphan_revision() -> None:
    args = argparse.Namespace()
    with (
        patch("migrations.cli.get_alembic_config"),
        patch("migrations.cli.ScriptDirectory.from_config") as mock_sd,
        patch("migrations.cli.MigrationContext") as mock_ctx,
    ):
        mock_sd.return_value.get_heads.return_value = ["20260829000001"]
        mock_sd.return_value.get_current_head.return_value = "20260829000001"
        # Codebase only knows 20260829000001
        mock_rev = MagicMock()
        mock_rev.revision = "20260829000001"
        mock_sd.return_value.walk_revisions.return_value = [mock_rev]

        # Database is at orphaned 20260829000002
        mock_ctx.configure.return_value.get_current_revision.return_value = (
            "20260829000002"
        )

        with patch("migrations.utils.migration_engine.connect"):
            ret = cmd_status(args)
            assert ret == 1  # Returns 1 on orphan revision detected


def test_cmd_check_downgrades_on_current_repo() -> None:
    args = argparse.Namespace()
    assert cmd_check_downgrades(args) == 0


def test_run_migrations_aborts_on_orphan_revision() -> None:
    with (
        patch("migrations.run.wait_for_database", return_value=True),
        patch("migrations.run.get_alembic_config"),
        patch("migrations.run.inspect") as mock_inspect,
        patch("migrations.run.ScriptDirectory.from_config") as mock_sd,
        patch("migrations.run.engine.connect") as mock_conn,
    ):
        mock_inspect.return_value.get_table_names.return_value = ["alembic_version"]
        mock_sd.return_value.get_current_head.return_value = "20260829000001"

        # Codebase only knows 20260829000001
        mock_rev = MagicMock()
        mock_rev.revision = "20260829000001"
        mock_sd.return_value.walk_revisions.return_value = [mock_rev]

        # DB has unknown 20260829000002
        mock_conn.return_value.__enter__.return_value.execute.return_value.scalar.return_value = "20260829000002"

        success = run_migrations(action="upgrade", target="head")
        assert success is False


def test_run_migrations_executes_downgrade() -> None:
    with (
        patch("migrations.run.wait_for_database", return_value=True),
        patch("migrations.run.get_alembic_config") as mock_cfg,
        patch("migrations.run.inspect") as mock_inspect,
        patch("migrations.run.ScriptDirectory.from_config") as mock_sd,
        patch("migrations.run.command.downgrade") as mock_down,
        patch("migrations.run.MigrationContext") as mock_ctx,
    ):
        mock_inspect.return_value.get_table_names.return_value = ["alembic_version"]
        mock_sd.return_value.get_current_head.return_value = "20260829000003"

        mock_rev1 = MagicMock()
        mock_rev1.revision = "20260829000001"
        mock_rev3 = MagicMock()
        mock_rev3.revision = "20260829000003"
        mock_sd.return_value.walk_revisions.return_value = [mock_rev3, mock_rev1]

        mock_ctx.configure.return_value.get_current_revision.return_value = (
            "20260829000001"
        )

        with patch("migrations.run.engine.connect") as mock_conn:
            mock_conn.return_value.__enter__.return_value.execute.return_value.scalar.return_value = "20260829000003"
            success = run_migrations(action="rollback", target="20260829000001")
            assert success is True
            mock_down.assert_called_once_with(mock_cfg.return_value, "20260829000001")
