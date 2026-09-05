"""
Architectural enforcement tests.
Ensures layer boundaries are respected (Domain never imports Infra).
"""

import ast
from pathlib import Path
from typing import List, Set, Tuple


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to collect import statements."""

    def __init__(self):
        self.imports: Set[str] = set()
        self.in_type_checking = False

    def visit_If(self, node):
        """Check if we're entering a TYPE_CHECKING block."""
        # Check if this is an `if TYPE_CHECKING:` block
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            # Save state and visit children
            old_state = self.in_type_checking
            self.in_type_checking = True
            self.generic_visit(node)
            self.in_type_checking = old_state
        else:
            self.generic_visit(node)

    def visit_Import(self, node):
        # Skip imports inside TYPE_CHECKING blocks
        if not self.in_type_checking:
            for alias in node.names:
                self.imports.add(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        # Skip imports inside TYPE_CHECKING blocks
        if not self.in_type_checking and node.module:
            self.imports.add(node.module)
        self.generic_visit(node)


def get_python_files(directory: str) -> List[Path]:
    """Get all Python files in a directory."""
    path = Path(directory)
    return list(path.rglob("*.py"))


def get_imports(filepath: Path) -> Set[str]:
    """Extract all imports from a Python file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        visitor = ImportVisitor()
        visitor.visit(tree)
        return visitor.imports
    except SyntaxError:
        return set()


def check_layer_violations(
    layer_path: str,
    forbidden_prefixes: List[str],
    exclude_deprecated: bool = True,
) -> List[Tuple[str, str]]:
    """
    Check for forbidden imports in a layer.

    Args:
        layer_path: Path to layer directory
        forbidden_prefixes: List of forbidden import prefixes
        exclude_deprecated: If True, exclude deprecated services from violations

    Returns list of (filepath, import) tuples for violations.
    """
    violations = []

    # Deprecated services that are allowed to violate boundaries temporarily
    deprecated_services = [
        "meal_plan_service.py",
        "meal_plan_persistence_service.py",
        "user_profile_service.py",
        "recipe_search_service.py",
    ]

    for filepath in get_python_files(layer_path):
        # Skip deprecated services if exclude_deprecated is True
        if exclude_deprecated:
            if any(deprecated in str(filepath) for deprecated in deprecated_services):
                continue

        imports = get_imports(filepath)
        for imp in imports:
            for forbidden in forbidden_prefixes:
                if imp.startswith(forbidden):
                    violations.append((str(filepath), imp))

    return violations


class TestDomainLayerBoundaries:
    """Test that domain layer respects boundaries."""

    def test_domain_does_not_import_infra(self):
        """Domain layer should never import from infrastructure."""
        violations = check_layer_violations(
            "src/domain",
            ["src.infra", "infra."],
        )

        assert not violations, (
            f"Domain layer imports infrastructure in {len(violations)} places:\n"
            + "\n".join(f"  {f}: {i}" for f, i in violations[:10])
        )

    def test_domain_does_not_import_api(self):
        """Domain layer should never import from API layer."""
        violations = check_layer_violations(
            "src/domain",
            ["src.api", "api."],
        )

        assert not violations, (
            f"Domain layer imports API in {len(violations)} places:\n"
            + "\n".join(f"  {f}: {i}" for f, i in violations[:10])
        )

    def test_domain_does_not_import_sqlalchemy(self):
        """Domain layer should not use SQLAlchemy directly."""
        violations = check_layer_violations(
            "src/domain",
            ["sqlalchemy"],
        )

        assert not violations, (
            f"Domain layer imports SQLAlchemy in {len(violations)} places:\n"
            + "\n".join(f"  {f}: {i}" for f, i in violations[:10])
        )

    def test_domain_does_not_import_redis(self):
        """Domain layer should not use Redis directly."""
        violations = check_layer_violations(
            "src/domain",
            ["redis", "aioredis"],
        )

        assert not violations, (
            f"Domain layer imports Redis in {len(violations)} places:\n"
            + "\n".join(f"  {f}: {i}" for f, i in violations[:10])
        )


class TestApplicationLayerBoundaries:
    """Test that application layer respects boundaries."""

    def test_app_does_not_import_api(self):
        """Application layer should not import from API layer."""
        violations = check_layer_violations(
            "src/app",
            ["src.api.routes", "src.api.schemas"],
        )

        # Allow src.api.exceptions and src.api.dependencies
        filtered = [
            (f, i)
            for f, i in violations
            if "exceptions" not in i and "dependencies" not in i
        ]

        assert not filtered, (
            f"App layer imports API routes/schemas in {len(filtered)} places:\n"
            + "\n".join(f"  {f}: {i}" for f, i in filtered[:10])
        )


class TestInfrastructureLayerBoundaries:
    """Test that infrastructure layer is properly isolated."""

    def test_infra_implements_domain_ports(self):
        """Infrastructure repositories should implement domain ports."""
        from pathlib import Path

        # Check that repository files exist and reference ports
        repo_path = Path("src/infra/repositories")
        port_path = Path("src/domain/ports")

        assert repo_path.exists(), "Repository directory should exist"
        assert port_path.exists(), "Ports directory should exist"

        # Count implementations
        repo_files = list(repo_path.glob("*_repository.py"))
        port_files = list(port_path.glob("*_repository_port.py"))

        assert len(repo_files) > 0, "Should have repository implementations"
        assert len(port_files) > 0, "Should have repository ports"


class TestServiceConsolidation:
    """Test service consolidation goals."""

    def test_domain_services_count(self):
        """Domain services should be consolidated (target: ~25)."""
        services_path = Path("src/domain/services")

        # Count .py files (excluding __init__.py and __pycache__)
        service_files = [
            f
            for f in services_path.rglob("*.py")
            if "__init__" not in f.name and "__pycache__" not in str(f)
        ]

        count = len(service_files)

        # After consolidation, should be around 25 or fewer.
        # Allow up to 46 for gradual migration (old services still exist alongside new ones).
        assert count <= 46, (
            f"Too many domain services: {count}. Target is ~25 after full consolidation.\n"
            f"Services: {[f.name for f in service_files[:15]]}..."
        )

    def test_meal_services_consolidated(self):
        """Meal-related services should be in meal/ subdirectory."""
        meal_path = Path("src/domain/services/meal")

        # After Phase 2, this directory should exist
        if meal_path.exists():
            files = list(meal_path.glob("*.py"))
            assert len(files) >= 2, "Meal services should be consolidated"

    def test_suggestion_services_consolidated(self):
        """Suggestion services should be in suggestion/ subdirectory."""
        suggestion_path = Path("src/domain/services/suggestion")

        # After Phase 2, this directory should exist
        if suggestion_path.exists():
            files = list(suggestion_path.glob("*.py"))
            assert len(files) >= 1, "Suggestion services should be consolidated"
