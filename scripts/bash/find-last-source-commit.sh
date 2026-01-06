#!/bin/bash
#
# find-last-source-commit.sh - Find the most recent commit that changed source files
#
# This script finds the commit hash of the most recent change to any of the
# specified file patterns. Used to determine if a rebuild is needed by comparing
# the last source-changing commit to available cached images.
#
# Usage:
#   find-last-source-commit.sh [OPTIONS] PATTERN [PATTERN...]
#
# Options:
#   --dir=PATH       Directory to search in (default: current directory)
#   --abbrev=N       Abbreviate commit hash to N characters (default: 8, use 40 for full)
#   --full           Output full 40-character commit hash (same as --abbrev=40)
#   --quiet          Only output the commit hash, no status messages
#   --help           Show this help message
#
# Examples:
#   # Find last commit that changed C++ source files
#   find-last-source-commit.sh "libraries/" "programs/" "CMakeLists.txt"
#
#   # Find last commit in a specific directory with full hash
#   find-last-source-commit.sh --dir=/path/to/repo --full "src/" "Dockerfile"
#
#   # Use with patterns file
#   find-last-source-commit.sh $(cat .source-patterns)
#
# Output:
#   Prints the (abbreviated) commit hash to stdout
#   Exit code 0 on success, 1 if no matching commits found
#
# Environment:
#   SOURCE_COMMIT_DIR      Alternative to --dir
#   SOURCE_COMMIT_ABBREV   Alternative to --abbrev
#

set -euo pipefail

# Defaults
DIR="${SOURCE_COMMIT_DIR:-.}"
ABBREV="${SOURCE_COMMIT_ABBREV:-8}"
QUIET="${QUIET:-false}"
PATTERNS=()

print_help() {
    sed -n '2,/^[^#]/p' "$0" | grep "^#" | sed 's/^# \?//'
}

log() {
    if [[ "$QUIET" != "true" ]]; then
        echo "$@" >&2
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir=*)
            DIR="${1#*=}"
            ;;
        --abbrev=*)
            ABBREV="${1#*=}"
            ;;
        --full)
            ABBREV=40
            ;;
        --quiet)
            QUIET=true
            ;;
        --help|-h)
            print_help
            exit 0
            ;;
        -*)
            echo "Error: Unknown option: $1" >&2
            print_help
            exit 2
            ;;
        *)
            PATTERNS+=("$1")
            ;;
    esac
    shift
done

# Validate inputs
if [[ ${#PATTERNS[@]} -eq 0 ]]; then
    echo "Error: At least one pattern is required" >&2
    print_help
    exit 2
fi

if [[ ! -d "$DIR" ]]; then
    echo "Error: Directory not found: $DIR" >&2
    exit 1
fi

# Change to target directory
cd "$DIR"

# Verify it's a git repository
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "Error: Not a git repository: $DIR" >&2
    exit 1
fi

log "Searching for last commit that changed: ${PATTERNS[*]}"

# Find the most recent commit that changed any of the patterns
COMMIT=$(git log --pretty=format:"%H" -n 1 -- "${PATTERNS[@]}" 2>/dev/null || true)

if [[ -z "$COMMIT" ]]; then
    log "Warning: No commits found matching patterns"
    # Fall back to HEAD if no matching commits (new repo or patterns don't match history)
    COMMIT=$(git rev-parse HEAD)
    log "Using HEAD: $COMMIT"
fi

# Abbreviate if requested
if [[ "$ABBREV" -lt 40 ]]; then
    SHORT_COMMIT=$(git -c core.abbrev="$ABBREV" rev-parse --short "$COMMIT")
else
    SHORT_COMMIT="$COMMIT"
fi

log "Found commit: $SHORT_COMMIT (from $COMMIT)"

# Output just the commit hash
echo "$SHORT_COMMIT"
