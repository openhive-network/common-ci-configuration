#!/bin/bash
#
# find-upstream-image.sh - Find the latest built image from an upstream repo
#
# This script fetches an upstream repository, finds commits that changed source
# files, and checks if a Docker image exists for those commits. It automatically
# falls back to older commits if the latest doesn't have an image yet (e.g., when
# upstream pipeline is still building after a squash merge).
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
#   --max-search=N       Max commits to check for existing image (default: 10)
#   --image=NAME         Image name within registry (default: none)
#   --output=FILE        Output env file (default: upstream-image.env)
#   --work-dir=PATH      Working directory for git clone (default: /tmp/upstream-repo-$$)
#   --keep-repo          Don't delete cloned repo after completion
#   --require-hit        Exit with error if no image found after checking all commits
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
#   UPSTREAM_COMMIT=<hash>           Full 40-char commit hash (for cache keys)
#   UPSTREAM_TAG=<tag>               Abbreviated commit for image tag (8 chars)
#   UPSTREAM_IMAGE=<full name>       Full image name with tag
#   UPSTREAM_REGISTRY=<path>         Registry path without tag
#   UPSTREAM_BRANCH=<branch>         Branch that was checked
#   UPSTREAM_FALLBACK=true|false     Whether a fallback commit was used
#
# Exit Codes:
#   0 - Success (image found, possibly via fallback)
#   1 - No image found after checking all commits (with --require-hit) or git/docker error
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
MAX_SEARCH=10
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
        --max-search=*)
            MAX_SEARCH="${1#*=}"
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

# Find source commits (full 40-char hashes for cache keys)
# Get multiple commits so we can fall back if the latest doesn't have an image yet
log "Finding source commits for patterns: ${PATTERN_ARRAY[*]}"
log "Will check up to $MAX_SEARCH commits for existing images"

# Get list of commits that changed source files (most recent first)
# Use git -C to avoid changing directories (keeps OUTPUT_FILE path correct)
mapfile -t SOURCE_COMMITS < <(git -C "$WORK_DIR" log --pretty=format:"%H" -n "$MAX_SEARCH" -- "${PATTERN_ARRAY[@]}" 2>/dev/null || true)

if [[ ${#SOURCE_COMMITS[@]} -eq 0 ]]; then
    error "Failed to find any source commits matching patterns"
    exit 1
fi

log "Found ${#SOURCE_COMMITS[@]} source commit(s) to check"

# Iterate through commits, looking for one with an existing image
FOUND_IMAGE=false
USED_FALLBACK=false
CHECKED_COUNT=0
FOUND_COMMIT=""

for COMMIT in "${SOURCE_COMMITS[@]}"; do
    CHECKED_COUNT=$((CHECKED_COUNT + 1))

    if [[ $CHECKED_COUNT -eq 1 ]]; then
        log "Checking latest commit: $COMMIT"
    else
        log "Checking fallback commit $CHECKED_COUNT: $COMMIT"
    fi

    # Build args for get-cached-image.sh (without --require-hit, we handle that ourselves)
    GET_IMAGE_ARGS=(
        --commit="$COMMIT"
        --registry="$REGISTRY"
        --output="$OUTPUT_FILE.tmp"
    )

    if [[ -n "$IMAGE" ]]; then
        GET_IMAGE_ARGS+=(--image="$IMAGE")
    fi

    # Always use quiet mode for fallback checks to reduce noise
    if [[ "$QUIET" == "true" ]] || [[ $CHECKED_COUNT -gt 1 ]]; then
        GET_IMAGE_ARGS+=(--quiet)
    fi

    # Check if image exists for this commit
    "$SCRIPT_DIR/get-cached-image.sh" "${GET_IMAGE_ARGS[@]}" || true

    # Check the result
    if [[ -f "$OUTPUT_FILE.tmp" ]] && grep -q "CACHE_HIT=true" "$OUTPUT_FILE.tmp"; then
        FOUND_IMAGE=true
        FOUND_COMMIT="$COMMIT"
        if [[ $CHECKED_COUNT -gt 1 ]]; then
            USED_FALLBACK=true
            log "Found image at fallback commit $CHECKED_COUNT: $COMMIT"
        else
            log "Found image at latest commit: $COMMIT"
        fi
        break
    else
        log "  No image found for $COMMIT"
        rm -f "$OUTPUT_FILE.tmp"
    fi
done

# Handle the result
if [[ "$FOUND_IMAGE" == "true" ]]; then
    # Read the temp output and rewrite with UPSTREAM_ prefix
    {
        echo "UPSTREAM_BRANCH=$BRANCH"
        echo "UPSTREAM_FALLBACK=$USED_FALLBACK"
        # Rename variables with UPSTREAM_ prefix
        sed 's/^CACHE_HIT=/UPSTREAM_CACHE_HIT=/;
             s/^IMAGE_COMMIT=/UPSTREAM_COMMIT=/;
             s/^IMAGE_TAG=/UPSTREAM_TAG=/;
             s/^IMAGE_NAME=/UPSTREAM_IMAGE=/;
             s/^IMAGE_REGISTRY=/UPSTREAM_REGISTRY=/' "$OUTPUT_FILE.tmp"
    } > "$OUTPUT_FILE"
    rm -f "$OUTPUT_FILE.tmp"

    if [[ "$USED_FALLBACK" == "true" ]]; then
        log ""
        log "NOTE: Using fallback image (latest commit's image not yet available)"
        log "      Latest source commit: ${SOURCE_COMMITS[0]}"
        log "      Using image from:     $FOUND_COMMIT"
    fi

    log ""
    log "Output written to: $OUTPUT_FILE"
    if [[ "$QUIET" != "true" ]]; then
        cat "$OUTPUT_FILE" >&2
    fi

    exit 0
else
    # No image found for any commit
    log ""
    log "ERROR: No image found after checking $CHECKED_COUNT commit(s)"
    log "       Latest source commit: ${SOURCE_COMMITS[0]}"
    log "       This usually means the upstream pipeline hasn't finished building yet."

    # Write output with CACHE_HIT=false for the latest commit
    LATEST_COMMIT="${SOURCE_COMMITS[0]}"
    LATEST_TAG="${LATEST_COMMIT:0:8}"
    if [[ -n "$IMAGE" ]]; then
        FULL_IMAGE="${REGISTRY}/${IMAGE}:${LATEST_TAG}"
        FULL_REGISTRY="${REGISTRY}/${IMAGE}"
    else
        FULL_IMAGE="${REGISTRY}:${LATEST_TAG}"
        FULL_REGISTRY="${REGISTRY}"
    fi

    cat > "$OUTPUT_FILE" << EOF
UPSTREAM_BRANCH=$BRANCH
UPSTREAM_FALLBACK=false
UPSTREAM_CACHE_HIT=false
UPSTREAM_COMMIT=$LATEST_COMMIT
UPSTREAM_TAG=$LATEST_TAG
UPSTREAM_IMAGE=$FULL_IMAGE
UPSTREAM_REGISTRY=$FULL_REGISTRY
EOF

    log ""
    log "Output written to: $OUTPUT_FILE"
    if [[ "$QUIET" != "true" ]]; then
        cat "$OUTPUT_FILE" >&2
    fi

    if [[ "$REQUIRE_HIT" == "true" ]]; then
        error "No upstream image available (--require-hit specified)"
        exit 1
    fi

    exit 0
fi
