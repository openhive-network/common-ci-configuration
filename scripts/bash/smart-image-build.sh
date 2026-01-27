#!/bin/bash
#
# smart-image-build.sh - Build or re-tag Docker images intelligently
#
# This script implements smart image building with automatic re-tagging to ensure
# downstream repos can always find images within their search depth. When source
# files haven't changed but many commits have been pushed (e.g., tests-only changes),
# it re-tags the cached image to a newer commit to keep it within the search window.
#
# Decision Logic:
#   When source files changed: full build required
#   When only tests/docs changed (--skip-build):
#     - Small gap (<RETAG_THRESHOLD): use cached image as-is
#     - Medium gap (RETAG_THRESHOLD to SEARCH_DEPTH): re-tag cached image
#     - Large gap (>SEARCH_DEPTH): force rebuild (fail-safe)
#
# Usage:
#   smart-image-build.sh [OPTIONS]
#
# Required Options:
#   --registry=URL           Docker registry (e.g., registry.gitlab.syncad.com/hive/haf)
#   --patterns=LIST          Comma-separated source patterns that trigger builds
#   --commit=SHA             Current commit SHA
#
# Conditional Options (required when --skip-build is set):
#   --cached-commit=SHA      Cached image commit SHA
#   --cached-image=NAME      Full cached image name with tag
#
# Optional:
#   --skip-build             Indicate build can be skipped (only tests/docs changed)
#   --retag-threshold=N      Re-tag when gap > N (default: 20)
#   --search-depth=N         Force rebuild when gap > N (default: 25)
#   --image=NAME             Image name within registry (default: root)
#   --abbrev=N               Commit abbreviation length for tags (default: 8)
#   --output=FILE            Output env file (default: smart_image_build.env)
#   --quiet                  Suppress status messages
#   --help                   Show this help message
#
# Examples:
#   # Source files changed - always builds
#   smart-image-build.sh \
#     --registry=registry.gitlab.syncad.com/hive/haf \
#     --patterns="src/,cmake/,Dockerfile" \
#     --commit=abc12345
#
#   # Only tests changed with cached image - decides skip/retag/build
#   smart-image-build.sh \
#     --registry=registry.gitlab.syncad.com/hive/haf \
#     --patterns="src/,cmake/,Dockerfile" \
#     --commit=abc12345 \
#     --skip-build \
#     --cached-commit=def67890 \
#     --cached-image=registry.gitlab.syncad.com/hive/haf:def67890
#
# Output Environment File (smart_image_build.env):
#   BUILD_ACTION=build|retag|skip   Decision: full build, re-tag, or use cached
#   IMAGE_COMMIT=<sha>              Commit SHA to use for the image
#   IMAGE_TAG=<tag>                 Tag (abbreviated commit)
#   IMAGE_NAME=<full name>          Full image name with tag
#   IMAGE_REGISTRY=<path>           Registry path without tag
#   CACHED_IMAGE_NAME=<name>        Original cached image (when retag/skip)
#   GAP_SIZE=<N>                    Number of source-commits between cached and current
#
# Exit Codes:
#   0 - Success
#   1 - Error during execution
#   2 - Invalid arguments
#

set -euo pipefail

# Defaults
REGISTRY=""
PATTERNS=""
COMMIT=""
CACHED_COMMIT=""
CACHED_IMAGE=""
SKIP_BUILD=false
RETAG_THRESHOLD=20
SEARCH_DEPTH=25
IMAGE=""
ABBREV=8
OUTPUT_FILE="smart_image_build.env"
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

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --registry=*)
            REGISTRY="${1#*=}"
            ;;
        --patterns=*)
            PATTERNS="${1#*=}"
            ;;
        --commit=*)
            COMMIT="${1#*=}"
            ;;
        --cached-commit=*)
            CACHED_COMMIT="${1#*=}"
            ;;
        --cached-image=*)
            CACHED_IMAGE="${1#*=}"
            ;;
        --skip-build)
            SKIP_BUILD=true
            ;;
        --retag-threshold=*)
            RETAG_THRESHOLD="${1#*=}"
            ;;
        --search-depth=*)
            SEARCH_DEPTH="${1#*=}"
            ;;
        --image=*)
            IMAGE="${1#*=}"
            ;;
        --abbrev=*)
            ABBREV="${1#*=}"
            ;;
        --output=*)
            OUTPUT_FILE="${1#*=}"
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
if [[ -z "$REGISTRY" ]]; then
    error "--registry=URL is required"
    exit 2
fi

if [[ -z "$PATTERNS" ]]; then
    error "--patterns=LIST is required"
    exit 2
fi

if [[ -z "$COMMIT" ]]; then
    error "--commit=SHA is required"
    exit 2
fi

# Validate conditional requirements
if [[ "$SKIP_BUILD" == "true" ]]; then
    if [[ -z "$CACHED_COMMIT" ]]; then
        error "--cached-commit=SHA is required when --skip-build is set"
        exit 2
    fi
    if [[ -z "$CACHED_IMAGE" ]]; then
        error "--cached-image=NAME is required when --skip-build is set"
        exit 2
    fi
fi

# Remove trailing slash from registry
REGISTRY="${REGISTRY%/}"

# Build registry path
if [[ -n "$IMAGE" ]]; then
    IMAGE_REGISTRY="${REGISTRY}/${IMAGE}"
else
    IMAGE_REGISTRY="${REGISTRY}"
fi

# Build current commit tag
if [[ ${#COMMIT} -gt $ABBREV ]]; then
    CURRENT_TAG="${COMMIT:0:$ABBREV}"
else
    CURRENT_TAG="$COMMIT"
fi
CURRENT_IMAGE="${IMAGE_REGISTRY}:${CURRENT_TAG}"

# Convert comma-separated patterns to array for git
IFS=',' read -ra PATTERN_ARRAY <<< "$PATTERNS"

# =============================================================================
# DECISION LOGIC
# =============================================================================

BUILD_ACTION=""
IMAGE_COMMIT=""
IMAGE_TAG=""
IMAGE_NAME=""
GAP_SIZE=0

if [[ "$SKIP_BUILD" != "true" ]]; then
    # Source files changed - full build required
    BUILD_ACTION="build"
    IMAGE_COMMIT="$COMMIT"
    IMAGE_TAG="$CURRENT_TAG"
    IMAGE_NAME="$CURRENT_IMAGE"
    log "Source files changed - full build required"

elif [[ -z "$CACHED_COMMIT" ]]; then
    # No cached image available - must build
    BUILD_ACTION="build"
    IMAGE_COMMIT="$COMMIT"
    IMAGE_TAG="$CURRENT_TAG"
    IMAGE_NAME="$CURRENT_IMAGE"
    log "No cached image available - full build required"

else
    # Calculate gap: source-changing commits between cached and current
    # Only count commits that actually changed source files (not tests/docs)
    log "Calculating gap between cached commit ($CACHED_COMMIT) and current ($COMMIT)..."
    log "Source patterns: ${PATTERN_ARRAY[*]}"

    # Use git rev-list to count commits that changed source files
    # Note: This assumes we're in a git repo with the full history fetched
    if GAP_SIZE=$(git rev-list --count "$CACHED_COMMIT".."$COMMIT" -- "${PATTERN_ARRAY[@]}" 2>/dev/null); then
        log "Gap size: $GAP_SIZE source-changing commits"
    else
        # Git command failed - might not have full history or invalid refs
        # Fall back to a safe default (trigger rebuild)
        log "WARNING: Could not calculate gap (git rev-list failed)"
        log "         This may happen if history is shallow or refs are invalid"
        GAP_SIZE=$((SEARCH_DEPTH + 1))
    fi

    if [[ $GAP_SIZE -le $RETAG_THRESHOLD ]]; then
        # Small gap - cached image is close enough, downstream will find it
        BUILD_ACTION="skip"
        IMAGE_COMMIT="$CACHED_COMMIT"
        # Extract tag from cached image
        IMAGE_TAG="${CACHED_IMAGE##*:}"
        IMAGE_NAME="$CACHED_IMAGE"
        log "Gap ($GAP_SIZE) <= threshold ($RETAG_THRESHOLD) - using cached image"

    elif [[ $GAP_SIZE -le $SEARCH_DEPTH ]]; then
        # Medium gap - re-tag to keep image within search window
        BUILD_ACTION="retag"
        IMAGE_COMMIT="$COMMIT"
        IMAGE_TAG="$CURRENT_TAG"
        IMAGE_NAME="$CURRENT_IMAGE"
        log "Gap ($GAP_SIZE) > threshold ($RETAG_THRESHOLD) but <= search depth ($SEARCH_DEPTH) - re-tagging"

    else
        # Large gap (fail-safe) - force full rebuild
        BUILD_ACTION="build"
        IMAGE_COMMIT="$COMMIT"
        IMAGE_TAG="$CURRENT_TAG"
        IMAGE_NAME="$CURRENT_IMAGE"
        log "WARNING: Gap ($GAP_SIZE) exceeds search depth ($SEARCH_DEPTH) - forcing full rebuild"
        log "         This is a fail-safe to ensure downstream repos can find the image"
    fi
fi

# =============================================================================
# OUTPUT
# =============================================================================

log ""
log "=== Decision ==="
log "  BUILD_ACTION: $BUILD_ACTION"
log "  IMAGE_COMMIT: $IMAGE_COMMIT"
log "  IMAGE_TAG: $IMAGE_TAG"
log "  IMAGE_NAME: $IMAGE_NAME"
log "  GAP_SIZE: $GAP_SIZE"

# Write output environment file
cat > "$OUTPUT_FILE" << EOF
BUILD_ACTION=$BUILD_ACTION
IMAGE_COMMIT=$IMAGE_COMMIT
IMAGE_TAG=$IMAGE_TAG
IMAGE_NAME=$IMAGE_NAME
IMAGE_REGISTRY=$IMAGE_REGISTRY
CACHED_IMAGE_NAME=${CACHED_IMAGE:-}
GAP_SIZE=$GAP_SIZE
EOF

log ""
log "Output written to: $OUTPUT_FILE"

exit 0
