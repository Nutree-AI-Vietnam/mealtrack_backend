"""
Architecture guardrails for the single-owner logger policy.

These tests scan source files to prevent regression into banned patterns.
They do NOT execute runtime code — they operate purely on text/AST.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[3]  # repo root
SRC = ROOT / "src"
API_ROUTES = SRC / "api" / "routes"


# ---------------------------------------------------------------------------
# Sentry SDK isolation
# ---------------------------------------------------------------------------


class TestSentryIsolation:
    """sentry_sdk must only be imported inside src/infra/monitoring/sentry.py."""

    def test_no_direct_sentry_sdk_outside_connector(self):
        allowed = SRC / "infra" / "monitoring" / "sentry.py"
        violations = []
        for py_file in SRC.rglob("*.py"):
            if py_file == allowed:
                continue
            text = py_file.read_text()
            if re.search(r"\bimport sentry_sdk\b|sentry_sdk\.", text):
                violations.append(str(py_file.relative_to(ROOT)))

        assert violations == [], (
            "Direct sentry_sdk usage must stay in src/infra/monitoring/sentry.py. "
            f"Violations: {violations}"
        )


# ---------------------------------------------------------------------------
# Route log-and-rethrow pattern
# ---------------------------------------------------------------------------


_ROUTE_LOG_AND_RETHROW_RE = re.compile(
    r"logger\.(error|exception)\b.*\n.*raise handle_exception\(",
    re.MULTILINE,
)


class TestRouteLogAndRethrow:
    """Route handlers must not log-and-rethrow: that creates duplicate ERROR signals."""

    def test_no_logger_error_immediately_before_handle_exception_in_routes(self):
        violations = []
        for py_file in API_ROUTES.rglob("*.py"):
            text = py_file.read_text()
            if _ROUTE_LOG_AND_RETHROW_RE.search(text):
                violations.append(str(py_file.relative_to(ROOT)))

        assert violations == [], (
            "Route files must not call logger.error/exception and then raise "
            "handle_exception on the next line — this creates duplicate ERRORs. "
            f"Violations: {violations}"
        )


# ---------------------------------------------------------------------------
# Banned sensitive substrings in log calls
# ---------------------------------------------------------------------------

# These patterns in log strings leak PII or secrets into log aggregators.
# Use specific patterns to avoid false positives (e.g., "Invalid webhook authorization"
# is safe; logging headers["Authorization"] is not).
_BANNED_LOG_PATTERNS = [
    # Food payload dumping — variable name in log call
    r"logger\.\w+\(.*food_item_changes",
    r"logger\.\w+\(.*content\[:",
    # Raw image / URL leaking
    r"logger\.\w+\(.*image_url",
    # Auth header VALUE leaking (quoted key or variable reference — not just the word)
    r'logger\.\w+\(.*["\']Authorization["\']',
    r"logger\.\w+\(.*authorization_header",
    # Bearer token value being logged (followed by a token character)
    r"logger\.\w+\(.*Bearer\s+[A-Za-z0-9]",
    # Email address leaking in logs
    r'logger\.\w+\(.*"Email sent to',
    r'logger\.\w+\(.*"Failed to send email to',
]


class TestSensitiveLogSubstrings:
    """Log call sites must not contain banned sensitive substrings."""

    def test_no_banned_sensitive_log_patterns_in_src(self):
        combined = re.compile(
            "|".join(_BANNED_LOG_PATTERNS),
            re.IGNORECASE,
        )
        violations = []
        for py_file in SRC.rglob("*.py"):
            text = py_file.read_text()
            for lineno, line in enumerate(text.splitlines(), 1):
                if combined.search(line):
                    violations.append(
                        f"{py_file.relative_to(ROOT)}:{lineno}: {line.strip()}"
                    )

        assert violations == [], (
            "Banned sensitive substrings found in log calls:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# No logging.basicConfig outside module entrypoints
# ---------------------------------------------------------------------------

_ENTRYPOINTS = {
    SRC / "api" / "main.py",
}


class TestNoBasicConfigOutsideEntrypoints:
    """logging.basicConfig must not appear in non-entrypoint source files."""

    def test_no_logging_basic_config_in_non_entrypoints(self):
        violations = []
        for py_file in SRC.rglob("*.py"):
            if py_file in _ENTRYPOINTS:
                continue
            text = py_file.read_text()
            if "logging.basicConfig" in text:
                violations.append(str(py_file.relative_to(ROOT)))

        assert violations == [], (
            "logging.basicConfig must only appear in module entrypoints. "
            f"Violations: {violations}"
        )
