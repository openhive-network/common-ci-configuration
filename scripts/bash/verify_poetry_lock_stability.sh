#!/bin/bash
#
# verify_poetry_lock_stability.sh - Verify poetry.lock contains only stable dependency versions
#
# This script checks if poetry.lock contains any unstable dependencies (dev, unstable, etc.).
# Helps ensure that stable branches use stable dependency versions.
#
# Usage:
#   ./verify_poetry_lock_stability.sh [options]
#
# Options:
#   --pyproject-dir <path>              Directory containing poetry.lock (default: current directory)
#   --unstable-version-pattern <regex>          Unstable version pattern to detect (default: "dev|unstable")
#   --allowed-package-pattern <regex>   Package name pattern to allow unstable versions (default: none)
#   -h, --help                          Show this help message
#
# Examples:
#   ./verify_poetry_lock_stability.sh
#   ./verify_poetry_lock_stability.sh --pyproject-dir /path/to/project
#   ./verify_poetry_lock_stability.sh --unstable-version-pattern "dev|alpha|beta|rc"
#   ./verify_poetry_lock_stability.sh --allowed-package-pattern "hiveio-.*"
#   ./verify_poetry_lock_stability.sh --unstable-version-pattern "dev" --allowed-package-pattern "hiveio-.*"
#
# Exit codes:
#   0 - No unstable dependencies found
#   1 - Unstable dependencies detected or error occurred
#

set -euo pipefail

# Colors for output
TXT_GREEN="${TXT_GREEN:-\e[1;32m}"
TXT_RED="${TXT_RED:-\e[1;31m}"
TXT_BLUE="${TXT_BLUE:-\e[1;34m}"
TXT_CLEAR="${TXT_CLEAR:-\e[0m}"

log_error() { echo -e "${TXT_RED}$*${TXT_CLEAR}"; }
log_success() { echo -e "${TXT_GREEN}$*${TXT_CLEAR}"; }
log_info() { echo -e "${TXT_BLUE}$*${TXT_CLEAR}"; }

show_help() {
    sed -n '2,/^[^#]/p' "$0" | grep "^#" | sed 's/^# \?//'
    exit 0
}

main() {
    local pyproject_dir="."
    local unstable_pattern="dev|unstable"
    local allowed_package_pattern=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --pyproject-dir)
                pyproject_dir="$2"
                shift 2
                ;;
            --unstable-version-pattern)
                unstable_pattern="$2"
                shift 2
                ;;
            --allowed-package-pattern)
                allowed_package_pattern="$2"
                shift 2
                ;;
            -h|--help)
                show_help
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    local lockfile="${pyproject_dir}/poetry.lock"

    log_info "Checking poetry.lock for unstable dependency versions..."
    log_info "  Unstable pattern: '${unstable_pattern}'"
    if [[ -n "${allowed_package_pattern}" ]]; then
        log_info "  Allowed package pattern: '${allowed_package_pattern}'"
    fi

    if [[ ! -f "${lockfile}" ]]; then
        log_success "No poetry.lock file found - skipping check"
        exit 0
    fi

    # Search for version lines matching unstable pattern, include package name (line before version)
    local unstable_deps
    unstable_deps=$(grep -B1 -E "^version = \".*\b(${unstable_pattern})" "${lockfile}" | grep -v "^--$" || true)

    # Filter out allowed packages if pattern is set
    local disallowed_deps=""
    local allowed_deps=""
    if [[ -n "${unstable_deps}" ]]; then
        # Format as "package = version" pairs
        local formatted_deps
        formatted_deps=$(echo "${unstable_deps}" | paste - - | sed 's/name = "\([^"]*\)".*version = "\([^"]*\)"/\1 = \2/')

        if [[ -n "${allowed_package_pattern}" ]]; then
            # Split into allowed and disallowed
            disallowed_deps=$(echo "${formatted_deps}" | grep -vE "^(${allowed_package_pattern}) = " || true)
            allowed_deps=$(echo "${formatted_deps}" | grep -E "^(${allowed_package_pattern}) = " || true)
        else
            disallowed_deps="${formatted_deps}"
        fi
    fi

    # Show allowed packages (info only)
    if [[ -n "${allowed_deps}" ]]; then
        echo ""
        log_info "Allowed unstable packages (matching pattern '${allowed_package_pattern}'):"
        echo "${allowed_deps}"
    fi

    if [[ -n "${disallowed_deps}" ]]; then
        echo ""
        log_error "========================================"
        log_error "ERROR: Unstable dependencies detected!"
        log_error "========================================"
        echo ""
        echo "The following unstable versions were found in poetry.lock:"
        echo ""
        echo "${disallowed_deps}"
        echo ""
        echo "Ensure affected packages are merged to stable branch first, then run:"
        echo "  poetry update <package> --lock"
        echo ""
        echo "This assumes pyproject.toml uses flexible constraints (e.g. >=1.28.0)."
        echo "If not, update the constraint in pyproject.toml manually or via 'poetry add'."
        exit 1
    fi

    if [[ -n "${allowed_deps}" ]]; then
        log_success "All unstable dependencies are allowed - check passed"
    else
        log_success "No unstable dependencies found in poetry.lock"
    fi
}

main "$@"
