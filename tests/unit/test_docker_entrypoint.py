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
    workers: str = "1",
    extra_env: dict[str, str] | None = None,
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
            "UVICORN_WORKERS": workers,
        }
    )
    environment.pop("WORKER_MAX_REQUESTS", None)
    environment.pop("WORKER_MAX_REQUESTS_JITTER", None)
    environment.update(extra_env or {})
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


def _uvicorn_command(commands: list[str]) -> str:
    return next(command for command in commands if command.startswith("uvicorn "))


def test_multiple_workers_are_recycled_after_jittered_request_counts(
    tmp_path: Path,
) -> None:
    result, commands = _run_entrypoint(tmp_path, render=True, workers="2")

    assert result.returncode == 0
    assert _uvicorn_command(commands).endswith(
        "--workers 2 --loop uvloop "
        "--limit-max-requests 50000 --limit-max-requests-jitter 25000"
    )
    assert "Worker recycling: after 50000-75000 requests" in result.stdout


def test_a_single_worker_is_never_recycled(tmp_path: Path) -> None:
    """With one worker, uvicorn has no supervisor; exiting would stop the server."""
    result, commands = _run_entrypoint(tmp_path, render=True, workers="1")

    assert result.returncode == 0
    assert "--limit-max-requests" not in _uvicorn_command(commands)
    assert "Worker recycling: off" in result.stdout


def test_recycling_limits_can_be_tuned_or_turned_off(tmp_path: Path) -> None:
    tuned_dir = tmp_path / "tuned"
    tuned_dir.mkdir()
    _, tuned = _run_entrypoint(
        tuned_dir,
        render=True,
        workers="4",
        extra_env={
            "WORKER_MAX_REQUESTS": "5000",
            "WORKER_MAX_REQUESTS_JITTER": "500",
        },
    )
    off_dir = tmp_path / "off"
    off_dir.mkdir()
    _, off = _run_entrypoint(
        off_dir,
        render=True,
        workers="4",
        extra_env={"WORKER_MAX_REQUESTS": "0"},
    )

    assert _uvicorn_command(tuned).endswith(
        "--limit-max-requests 5000 --limit-max-requests-jitter 500"
    )
    assert "--limit-max-requests" not in _uvicorn_command(off)


def test_non_numeric_recycling_limit_fails_before_startup(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        render=True,
        workers="2",
        extra_env={"WORKER_MAX_REQUESTS": "lots"},
    )

    assert result.returncode == 1
    assert "must be whole numbers" in result.stdout
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
