"""App entrypoint calls load_dotenv before Sentry initialization."""

import ast
from pathlib import Path

_MAIN = Path("src/api/main.py")


def _module_call_names() -> list[str]:
    tree = ast.parse(_MAIN.read_text())
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name):
            names.append(func.id)
    return names


def test_load_dotenv_runs_before_sentry_init():
    calls = _module_call_names()
    assert "load_dotenv" in calls
    assert "initialize_observability" in calls
    assert calls.index("load_dotenv") < calls.index("initialize_observability")
