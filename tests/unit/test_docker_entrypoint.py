from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).parents[2]
ENTRYPOINT = REPOSITORY_ROOT / "docker-entrypoint.sh"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(f"#!/bin/sh\n{contents}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_entrypoint(
    tmp_path: Path,
    *,
    auto_migrate: str | None = None,
    production_variable: str | None = None,
    render: bool = False,
    migration_exit_code: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    _write_executable(
        bin_dir / "python",
        'printf "python %s\\n" "$*" >> "$ENTRYPOINT_TEST_LOG"\n'
        'exit "$MIGRATION_EXIT_CODE"\n',
    )
    _write_executable(
        bin_dir / "uvicorn",
        'printf "uvicorn %s\\n" "$*" >> "$ENTRYPOINT_TEST_LOG"\n',
    )

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{os.defpath}",
            "ENTRYPOINT_TEST_LOG": str(command_log),
            "MIGRATION_EXIT_CODE": str(migration_exit_code),
            "PORT": "8000",
            "UVICORN_WORKERS": "1",
        }
    )
    environment.pop("ENV", None)
    environment.pop("ENVIRONMENT", None)
    environment.pop("RENDER", None)
    if auto_migrate is None:
        environment.pop("AUTO_MIGRATE", None)
    else:
        environment["AUTO_MIGRATE"] = auto_migrate
    if production_variable is not None:
        environment[production_variable] = "production"
    if render:
        environment["RENDER"] = "true"

    result = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = (
        command_log.read_text(encoding="utf-8").splitlines()
        if command_log.exists()
        else []
    )
    return result, commands


@pytest.mark.parametrize("value", ["true", "1", "YES", "on"])
def test_enabled_values_run_migrations(tmp_path: Path, value: str) -> None:
    result, commands = _run_entrypoint(tmp_path, auto_migrate=value)

    assert result.returncode == 0
    assert any(command == "python migrations/run.py" for command in commands)
    assert any(command.startswith("uvicorn ") for command in commands)


def test_unset_toggle_runs_migrations_by_default(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(tmp_path)

    assert result.returncode == 0
    assert any(command == "python migrations/run.py" for command in commands)


@pytest.mark.parametrize("value", ["false", "0", "NO", "off"])
def test_disabled_values_skip_migrations(tmp_path: Path, value: str) -> None:
    result, commands = _run_entrypoint(tmp_path, auto_migrate=value)

    assert result.returncode == 0
    assert not any(command.startswith("python ") for command in commands)
    assert any(command.startswith("uvicorn ") for command in commands)


@pytest.mark.parametrize("production_variable", ["ENV", "ENVIRONMENT"])
def test_production_skips_before_validating_toggle(
    tmp_path: Path, production_variable: str
) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        auto_migrate="not-a-boolean",
        production_variable=production_variable,
    )

    assert result.returncode == 0
    assert "GitHub Actions migrate workflow" in result.stdout
    assert not any(command.startswith("python ") for command in commands)


def test_render_skips_migrations_even_when_auto_migrate_true(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(tmp_path, auto_migrate="true", render=True)

    assert result.returncode == 0
    assert "Render; use GitHub Actions migrate workflow" in result.stdout
    assert not any(command.startswith("python ") for command in commands)
    assert any(command.startswith("uvicorn ") for command in commands)


def test_render_skips_before_validating_toggle(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        auto_migrate="not-a-boolean",
        render=True,
    )

    assert result.returncode == 0
    assert "Render; use GitHub Actions migrate workflow" in result.stdout
    assert not any(command.startswith("python ") for command in commands)


def test_invalid_non_production_toggle_fails_before_startup(
    tmp_path: Path,
) -> None:
    result, commands = _run_entrypoint(tmp_path, auto_migrate="not-a-boolean")

    assert result.returncode == 1
    assert "AUTO_MIGRATE must be one of" in result.stdout
    assert commands == []


def test_migration_failure_stops_application_startup(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        auto_migrate="true",
        migration_exit_code=7,
    )

    assert result.returncode == 1
    assert "Migrations failed!" in result.stdout
    assert commands == ["python migrations/run.py"]
