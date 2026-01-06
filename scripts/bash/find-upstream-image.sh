#!/bin/bash
#
# find-upstream-image.sh - Find the latest built image from an upstream repo
#
# This script fetches an upstream repository, finds the last commit that changed
# source files, and checks if a Docker image exists for that commit. Used by
# downstream repos to find pre-built images from their dependencies.
#
# Usage:
#   find-upstream-image.sh [OPTIONS]
#
# Required Options:
#   --repo-url=URL       Git URL of upstream repo (e.g., https://gitlab.syncad.com/hive/hive.git)
#   --registry=URL       Docker registry URL (e.g., registry.gitlab.syncad.com/hive/hive)
#   --patterns=LIST      Comma-separated list of source file patterns (e.g., "libraries/,programs/,Dockerfile")
#
# Optional:
#   --branch=NAME        Branch to check (default: develop)
#   --depth=N            Git fetch depth (default: 100)
#   --image=NAME         Image name within registry (default: none)
#   --output=FILE        Output env file (default: upstream-image.env)
#   --work-dir=PATH      Working directory for git clone (default: /tmp/upstream-repo-$$)
#   --keep-repo          Don't delete cloned repo after completion
#   --require-hit        Exit with error if image not found
#   --quiet              Suppress status messages
#   --help               Show this help message
#
# Examples:
#   # Find latest hive image for clive
#   find-upstream-image.sh \
#     --repo-url=https://gitlab.syncad.com/hive/hive.git \
#     --registry=registry.gitlab.syncad.com/hive/hive \
#     --patterns="libraries/,programs/,CMakeLists.txt,Dockerfile,cmake/,.gitmodules"
#
#   # Find testnet image from specific branch
#   find-upstream-image.sh \
#     --repo-url=https://gitlab.syncad.com/hive/hive.git \
#     --registry=registry.gitlab.syncad.com/hive/hive \
#     --image=testnet \
#     --branch=develop \
#     --patterns="libraries/,programs/"
#
# Output Environment File (upstream-image.env):
#   UPSTREAM_CACHE_HIT=true|false    Whether image was found
#   UPSTREAM_COMMIT=<hash>           Commit hash of last source change
#   UPSTREAM_TAG=<tag>               Docker image tag
#   UPSTREAM_IMAGE=<full name>       Full image name with tag
#   UPSTREAM_REGISTRY=<path>         Registry path without tag
#   UPSTREAM_BRANCH=<branch>         Branch that was checked
#
# Exit Codes:
#   0 - Success
#   1 - Image not found (with --require-hit) or git/docker error
#   2 - Invalid arguments
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
REPO_URL=""
REGISTRY=""
PATTERNS=""
BRANCH="develop"
DEPTH=100
IMAGE=""
OUTPUT_FILE="upstream-image.env"
WORK_DIR=""
KEEP_REPO=false
REQUIRE_HIT=false
QUIET="${QUIET:-false}"

print_help() {
    sed -n '2,/^[^#]/p' "$0" | grep "^#" | sed 's/^# \?//'
}

log() {
    if [[ "$QUIET" != "true" ]]; then
        echo "$@" >&2
    fi
}

error() {
    echo "Error: $*" >&2
}

# shellcheck disable=SC2329  # Function is invoked via trap
cleanup() {
    if [[ "$KEEP_REPO" != "true" && -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        log "Cleaning up: $WORK_DIR"
        rm -rf "$WORK_DIR"
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-url=*)
            REPO_URL="${1#*=}"
            ;;
        --registry=*)
            REGISTRY="${1#*=}"
            ;;
        --patterns=*)
            PATTERNS="${1#*=}"
            ;;
        --branch=*)
            BRANCH="${1#*=}"
            ;;
        --depth=*)
            DEPTH="${1#*=}"
            ;;
        --image=*)
            IMAGE="${1#*=}"
            ;;
        --output=*)
            OUTPUT_FILE="${1#*=}"
            ;;
        --work-dir=*)
            WORK_DIR="${1#*=}"
            ;;
        --keep-repo)
            KEEP_REPO=true
            ;;
        --require-hit)
            REQUIRE_HIT=true
            ;;
        --quiet)
            QUIET=true
            ;;
        --help|-h)
            print_help
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            print_help
            exit 2
            ;;
    esac
    shift
done

# Validate required arguments
if [[ -z "$REPO_URL" ]]; then
    error "--repo-url=URL is required"
    exit 2
fi

if [[ -z "$REGISTRY" ]]; then
    error "--registry=URL is required"
    exit 2
fi

if [[ -z "$PATTERNS" ]]; then
    error "--patterns=LIST is required"
    exit 2
fi

# Set default work directory
if [[ -z "$WORK_DIR" ]]; then
    WORK_DIR="/tmp/upstream-repo-$$"
fi

# Setup cleanup trap
trap cleanup EXIT

# Convert comma-separated patterns to array
IFS=',' read -ra PATTERN_ARRAY <<< "$PATTERNS"

log "Fetching upstream repo: $REPO_URL (branch: $BRANCH)"

# Clone the repository (shallow, single branch)
if [[ -d "$WORK_DIR" ]]; then
    log "Removing existing work directory: $WORK_DIR"
    rm -rf "$WORK_DIR"
fi

git clone --depth="$DEPTH" --branch="$BRANCH" --single-branch "$REPO_URL" "$WORK_DIR" 2>&1 | \
    while IFS= read -r line; do log "  $line"; done

# Find last source commit
log "Finding last source commit for patterns: ${PATTERN_ARRAY[*]}"

FIND_COMMIT_ARGS=(--dir="$WORK_DIR" --quiet)
COMMIT=$("$SCRIPT_DIR/find-last-source-commit.sh" "${FIND_COMMIT_ARGS[@]}" "${PATTERN_ARRAY[@]}")

if [[ -z "$COMMIT" ]]; then
    error "Failed to find source commit"
    exit 1
fi

log "Found last source commit: $COMMIT"

# Check if image exists
log "Checking for image in registry: $REGISTRY"

GET_IMAGE_ARGS=(
    --commit="$COMMIT"
    --registry="$REGISTRY"
    --output="$OUTPUT_FILE.tmp"
)

if [[ -n "$IMAGE" ]]; then
    GET_IMAGE_ARGS+=(--image="$IMAGE")
fi

if [[ "$QUIET" == "true" ]]; then
    GET_IMAGE_ARGS+=(--quiet)
fi

if [[ "$REQUIRE_HIT" == "true" ]]; then
    GET_IMAGE_ARGS+=(--require-hit)
fi

"$SCRIPT_DIR/get-cached-image.sh" "${GET_IMAGE_ARGS[@]}"
GET_RESULT=$?

# Read the temp output and rewrite with UPSTREAM_ prefix
if [[ -f "$OUTPUT_FILE.tmp" ]]; then
    {
        # Add branch info
        echo "UPSTREAM_BRANCH=$BRANCH"
        # Rename variables with UPSTREAM_ prefix
        sed 's/^CACHE_HIT=/UPSTREAM_CACHE_HIT=/;
             s/^IMAGE_COMMIT=/UPSTREAM_COMMIT=/;
             s/^IMAGE_TAG=/UPSTREAM_TAG=/;
             s/^IMAGE_NAME=/UPSTREAM_IMAGE=/;
             s/^IMAGE_REGISTRY=/UPSTREAM_REGISTRY=/' "$OUTPUT_FILE.tmp"
    } > "$OUTPUT_FILE"
    rm -f "$OUTPUT_FILE.tmp"
fi

log ""
log "Output written to: $OUTPUT_FILE"
if [[ "$QUIET" != "true" ]]; then
    cat "$OUTPUT_FILE" >&2
fi

exit $GET_RESULT
