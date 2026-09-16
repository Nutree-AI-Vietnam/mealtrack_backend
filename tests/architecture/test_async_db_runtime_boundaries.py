"""Guardrails for the async-only repository consolidation rollout."""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

ALLOWED_SYNC_DB_IMPORT_FILES = set()

ALLOWED_REPOSITORY_TRANSACTION_FILES = {
    "src/infra/repositories/pgvector_meal_image_cache_repository_async.py",
}

# Existing adapters that still use `requests` (sync HTTP) — tracked for Phase 3 removal.
# Do NOT add new entries here; fix the offender instead.
ALLOWED_REQUESTS_IMPORT_FILES = set()


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports_sync_db_boundary(path: Path) -> bool:
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names = {alias.name for alias in node.names}
            if module in {"src.infra.database.config", "src.infra.database.uow"}:
                return True
            if module == "sqlalchemy.orm" and "Session" in imported_names:
                return True
        if isinstance(node, ast.Import):
            if any(
                alias.name in {"src.infra.database.config", "src.infra.database.uow"}
                for alias in node.names
            ):
                return True
    return False


def _calls_transaction_boundary(path: Path) -> bool:
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"commit", "rollback"}:
                return True
    return False


def test_sync_db_runtime_imports_do_not_expand_during_transition() -> None:
    offenders = {
        _relative(path)
        for path in _python_files(SRC_ROOT)
        if _imports_sync_db_boundary(path)
        and _relative(path) not in {"src/infra/database/config.py"}
    }

    assert offenders == ALLOWED_SYNC_DB_IMPORT_FILES


def test_repository_transaction_boundary_allowlist_does_not_expand() -> None:
    repo_root = SRC_ROOT / "infra" / "repositories"
    offenders = {
        _relative(path)
        for path in _python_files(repo_root)
        if _calls_transaction_boundary(path)
    }

    assert offenders == ALLOWED_REPOSITORY_TRANSACTION_FILES


def test_orm_models_do_not_import_base_from_sync_config() -> None:
    offenders = []
    for path in _python_files(SRC_ROOT / "infra" / "database" / "models"):
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "src.infra.database.config"
            ):
                imported_names = {alias.name for alias in node.names}
                if "Base" in imported_names:
                    offenders.append(_relative(path))

    assert offenders == []


def test_tests_do_not_reintroduce_generic_sync_to_async_repo_wrapper() -> None:
    """Generic wrappers hide sync repositories behind awaitable methods."""
    forbidden = {
        "Async" + "SyncRepoWrapper",
        "Wraps a sync repository so every method is also " + "awaitable",
    }
    offenders: dict[str, list[str]] = {}
    for path in _python_files(PROJECT_ROOT / "tests"):
        text = path.read_text(encoding="utf-8")
        matches = [term for term in forbidden if term in text]
        if matches:
            offenders[_relative(path)] = matches

    assert offenders == {}


def test_legacy_sync_repositories_do_not_expose_async_compatibility_methods() -> None:
    offenders: dict[str, list[str]] = {}
    repo_root = SRC_ROOT / "infra" / "repositories"
    for path in _python_files(repo_root):
        if path.name.endswith("_async.py"):
            continue
        tree = _parse(path)
        async_defs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and (
                node.name.endswith("_async") or node.name in {"get_async", "add_async"}
            )
        ]
        if async_defs:
            offenders[_relative(path)] = async_defs

    assert offenders == {}


def test_food_reference_request_dependencies_use_async_adapter() -> None:
    """Request-path food-reference services must not wire the sync singleton."""
    offenders: dict[str, list[str]] = {}
    forbidden = {
        "food_ref_repo=get_food_reference_repository(",
        "food_reference_repository=get_food_reference_repository(",
        "food_reference_repository import FoodReferenceRepository",
    }
    for path in _python_files(SRC_ROOT / "api"):
        text = path.read_text(encoding="utf-8")
        matches = [term for term in forbidden if term in text]
        if matches:
            offenders[_relative(path)] = matches

    assert offenders == {}


def test_promo_referral_runtime_handlers_use_uow_repositories() -> None:
    """Request/handler code should use AsyncUnitOfWork-owned repositories."""
    offenders: dict[str, list[str]] = {}
    forbidden = {
        "PromoCodeRepository(",
        "ReferralRepository(",
        "promo_code_repository import PromoCodeRepository",
        "referral_repository import ReferralRepository",
    }
    scanned_roots = [
        SRC_ROOT / "app",
        SRC_ROOT / "api",
        SRC_ROOT / "cron",
        SRC_ROOT / "infra" / "services",
    ]

    for root in scanned_roots:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            matches = [term for term in forbidden if term in text]
            if matches:
                offenders[_relative(path)] = matches

    assert offenders == {}


def _getenv_calls_database_url_direct(tree: ast.AST) -> list[int]:
    """Return line numbers of os.getenv('DATABASE_URL_DIRECT') calls in the AST."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "DATABASE_URL_DIRECT"
            ):
                lines.append(getattr(node, "lineno", 0))
    return lines


def test_app_runtime_config_does_not_prefer_database_url_direct() -> None:
    """Guard: app runtime must not call os.getenv('DATABASE_URL_DIRECT')."""
    config_path = SRC_ROOT / "infra" / "database" / "config_async.py"
    hits = _getenv_calls_database_url_direct(_parse(config_path))
    assert hits == [], (
        f"config_async.py calls os.getenv('DATABASE_URL_DIRECT') at lines {hits}. "
        "DATABASE_URL_DIRECT is reserved for migration tooling. "
        "Use APP_DATABASE_URL > DATABASE_URL priority instead."
    )


def test_no_requests_imports_in_async_adapter_paths() -> None:
    """Guard: runtime adapter files must not import 'requests' (sync HTTP library).

    New adapters must use httpx. Files in ALLOWED_REQUESTS_IMPORT_FILES are
    tracked for Phase 3 migration; the allowlist must not grow.
    """
    adapters_root = SRC_ROOT / "infra" / "adapters"
    offenders: dict[str, list[int]] = {}
    for path in _python_files(adapters_root):
        relative = _relative(path)
        if relative in ALLOWED_REQUESTS_IMPORT_FILES:
            continue
        tree = _parse(path)
        lines_with_requests = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "requests" or alias.name.startswith("requests."):
                        lines_with_requests.append(getattr(node, "lineno", 0))
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("requests"):
                    lines_with_requests.append(getattr(node, "lineno", 0))
        if lines_with_requests:
            offenders[relative] = lines_with_requests

    assert offenders == {}, (
        f"These adapter files import 'requests' (sync HTTP library) "
        f"in async runtime paths: {offenders}. "
        "Replace with httpx or move to an explicit off-loop boundary."
    )
