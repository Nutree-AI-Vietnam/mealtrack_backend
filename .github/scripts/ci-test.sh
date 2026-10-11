#!/bin/bash

# CI Test Runner
# Handles test execution with coverage reporting

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[TEST]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration
COVERAGE_THRESHOLD="${COVERAGE_THRESHOLD:-70}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13.2}"

# Install dependencies
install_dependencies() {
    log_info "Installing dependencies..."
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    pip install -r requirements-test.txt
}

# Verify database connection
verify_database() {
    log_info "Verifying database connection..."

    DB_HOST="${DB_HOST:-127.0.0.1}"
    DB_PORT="${DB_PORT:-3306}"
    DB_USER="${DB_USER:-test_user}"
    DB_PASS="${DB_PASS:-test_password}"
    DB_NAME="${DB_NAME:-mealtrack_test}"

    # Wait for MySQL to be ready
    for i in {1..30}; do
        if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" -e "SELECT 1" "$DB_NAME" >/dev/null 2>&1; then
            log_info "✅ Database is ready"
            return 0
        fi
        log_warn "Waiting for database... ($i/30)"
        sleep 2
    done

    log_error "❌ Database connection failed"
    return 1
}

# Clean test database
clean_database() {
    log_info "Cleaning test database..."

    DB_HOST="${DB_HOST:-127.0.0.1}"
    DB_PORT="${DB_PORT:-3306}"
    DB_USER="${DB_USER:-test_user}"
    DB_PASS="${DB_PASS:-test_password}"
    DB_NAME="${DB_NAME:-mealtrack_test}"

    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" \
        -e "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME;" || true

    log_info "Database cleaned"
}

# Run tests with coverage
run_tests() {
    log_info "Running tests with coverage..."

    # Set test environment variables
    export CI=true
    export TESTING=true
    export PYTHONPATH=.
    export USE_MOCK_STORAGE=1
    export USE_MOCK_VISION_SERVICE=1

    # Use provided or mock values for API keys
    export GOOGLE_API_KEY="${GOOGLE_API_KEY:-mock-key-for-testing}"
    export CLOUDINARY_CLOUD_NAME="${CLOUDINARY_CLOUD_NAME:-mock-cloud}"
    export CLOUDINARY_API_KEY="${CLOUDINARY_API_KEY:-mock-api-key}"
    export CLOUDINARY_API_SECRET="${CLOUDINARY_API_SECRET:-mock-api-secret}"
    export PINECONE_API_KEY="${PINECONE_API_KEY:-mock-api-key}"

    # Run pytest with coverage (exclude integration tests)
    pytest \
        --cov=src \
        --cov-report=xml \
        --cov-report=term-missing \
        --cov-report=html \
        --ignore=tests/integration \
        -m "not integration" \
        -n auto \
        --maxfail=5 \
        -v

    local test_exit_code=$?

    if [ $test_exit_code -eq 0 ]; then
        log_info "✅ All tests passed"
    else
        log_error "❌ Some tests failed"
    fi

    return $test_exit_code
}

# Check coverage threshold
check_coverage() {
    log_info "Checking test coverage (threshold: ${COVERAGE_THRESHOLD}%)..."

    if coverage report --fail-under="$COVERAGE_THRESHOLD"; then
        log_info "✅ Coverage threshold met"
        return 0
    else
        log_warn "⚠️ Coverage below threshold"
        return 1
    fi
}

# Generate coverage report
generate_report() {
    log_info "Generating coverage reports..."

    # Generate JSON report for CI
    coverage json -o coverage.json

    # Summary for GitHub
    echo "## 📊 Test Coverage Report" >> test_summary.md
    echo "" >> test_summary.md
    coverage report --format=markdown >> test_summary.md || coverage report >> test_summary.md

    log_info "Coverage reports generated:"
    log_info "  - coverage.xml (Codecov)"
    log_info "  - coverage.json (JSON)"
    log_info "  - htmlcov/ (HTML)"
    log_info "  - test_summary.md (Markdown)"
}

# Main execution
main() {
    ACTION="${1:-all}"

    case "$ACTION" in
        install)
            install_dependencies
            ;;
        verify-db)
            verify_database
            ;;
        clean-db)
            clean_database
            ;;
        test)
            run_tests
            ;;
        coverage)
            check_coverage
            ;;
        report)
            generate_report
            ;;
        all)
            install_dependencies
            # Skip database verification and cleaning in CI - tests handle their own DB setup
            if [ -z "$CI" ]; then
                verify_database
                clean_database
            fi
            run_tests
            generate_report
            check_coverage
            ;;
        *)
            echo "Usage: $0 {install|verify-db|clean-db|test|coverage|report|all}"
            exit 1
            ;;
    esac
}

main "$@"
