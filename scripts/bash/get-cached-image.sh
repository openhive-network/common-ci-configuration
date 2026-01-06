#!/bin/bash
#
# get-cached-image.sh - Check if a Docker image exists for a given commit
#
# This script checks if a Docker image tagged with a specific commit hash exists
# in a container registry. Used by both building repos (to avoid rebuilds) and
# downstream repos (to find pre-built images from upstream).
#
# Usage:
#   get-cached-image.sh [OPTIONS]
#
# Required Options (one of):
#   --commit=HASH        Commit hash to look up (can be short or full)
#   --commit-var=NAME    Environment variable containing commit hash
#
# Required Options:
#   --registry=URL       Registry URL (e.g., registry.gitlab.syncad.com/hive/hive)
#
# Optional:
#   --image=NAME         Image name within registry (default: none, uses registry directly)
#   --output=FILE        Output file for environment variables (default: image-cache.env)
#   --tag-prefix=PREFIX  Prefix for tags (default: none)
#   --tag-suffix=SUFFIX  Suffix for tags (default: none)
#   --abbrev=N           Abbreviate commit to N chars for tag (default: 8)
#   --require-hit        Exit with error if image not found (default: exit 0 with CACHE_HIT=false)
#   --quiet              Suppress status messages
#   --help               Show this help message
#
# Examples:
#   # Check if hive image exists for a commit
#   get-cached-image.sh --commit=abc12345 --registry=registry.gitlab.syncad.com/hive/hive
#
#   # Check for testnet variant
#   get-cached-image.sh --commit=abc12345 --registry=registry.gitlab.syncad.com/hive/hive --image=testnet
#
#   # Use commit from environment variable
#   export HIVE_COMMIT=abc12345
#   get-cached-image.sh --commit-var=HIVE_COMMIT --registry=registry.gitlab.syncad.com/hive/hive
#
#   # Downstream repo looking up upstream image
#   HIVE_COMMIT=$(find-last-source-commit.sh --dir=/tmp/hive libraries/ programs/)
#   get-cached-image.sh --commit=$HIVE_COMMIT --registry=registry.gitlab.syncad.com/hive/hive
#
# Output Environment File (image-cache.env):
#   CACHE_HIT=true|false       Whether image was found
#   IMAGE_COMMIT=<full hash>   Full commit hash
#   IMAGE_TAG=<tag>            Tag used for lookup (may be abbreviated)
#   IMAGE_NAME=<full name>     Full image name with tag (registry/image:tag)
#   IMAGE_REGISTRY=<path>      Registry path without tag
#
# Exit Codes:
#   0 - Success (image found or not found without --require-hit)
#   1 - Error (image not found with --require-hit, or other error)
#   2 - Invalid arguments
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source docker utilities if available
if [[ -f "$SCRIPT_DIR/docker-image-utils.sh" ]]; then
    # shellcheck source=./docker-image-utils.sh disable=SC1091
    source "$SCRIPT_DIR/docker-image-utils.sh"
fi

# Defaults
COMMIT=""
COMMIT_VAR=""
REGISTRY=""
IMAGE=""
OUTPUT_FILE="image-cache.env"
TAG_PREFIX=""
TAG_SUFFIX=""
ABBREV=8
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

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --commit=*)
            COMMIT="${1#*=}"
            ;;
        --commit-var=*)
            COMMIT_VAR="${1#*=}"
            ;;
        --registry=*)
            REGISTRY="${1#*=}"
            ;;
        --image=*)
            IMAGE="${1#*=}"
            ;;
        --output=*)
            OUTPUT_FILE="${1#*=}"
            ;;
        --tag-prefix=*)
            TAG_PREFIX="${1#*=}"
            ;;
        --tag-suffix=*)
            TAG_SUFFIX="${1#*=}"
            ;;
        --abbrev=*)
            ABBREV="${1#*=}"
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

# Get commit from variable if specified
if [[ -n "$COMMIT_VAR" ]]; then
    COMMIT="${!COMMIT_VAR:-}"
    if [[ -z "$COMMIT" ]]; then
        error "Environment variable $COMMIT_VAR is not set or empty"
        exit 2
    fi
fi

# Validate required arguments
if [[ -z "$COMMIT" ]]; then
    error "Either --commit=HASH or --commit-var=NAME is required"
    print_help
    exit 2
fi

if [[ -z "$REGISTRY" ]]; then
    error "--registry=URL is required"
    print_help
    exit 2
fi

# Store full commit (might already be abbreviated, that's ok)
FULL_COMMIT="$COMMIT"

# Abbreviate commit for tag if longer than requested
if [[ ${#COMMIT} -gt $ABBREV ]]; then
    TAG_COMMIT="${COMMIT:0:$ABBREV}"
else
    TAG_COMMIT="$COMMIT"
fi

# Build the tag
TAG="${TAG_PREFIX}${TAG_COMMIT}${TAG_SUFFIX}"

# Build full image name
# Remove trailing slash from registry if present
REGISTRY="${REGISTRY%/}"

if [[ -n "$IMAGE" ]]; then
    IMAGE_REGISTRY="${REGISTRY}/${IMAGE}"
    IMAGE_NAME="${REGISTRY}/${IMAGE}:${TAG}"
else
    IMAGE_REGISTRY="${REGISTRY}"
    IMAGE_NAME="${REGISTRY}:${TAG}"
fi

log "Checking for image: $IMAGE_NAME"

# Check if image exists in registry
CACHE_HIT=false

# Save current set -e state and disable temporarily
OLD_SET_E=0
[[ $- == *e* ]] && OLD_SET_E=1
set +e

docker manifest inspect "$IMAGE_NAME" >/dev/null 2>&1
RESULT=$?

# Restore set -e if it was enabled
((OLD_SET_E)) && set -e

if [[ $RESULT -eq 0 ]]; then
    CACHE_HIT=true
    log "Image found: $IMAGE_NAME"
else
    log "Image not found: $IMAGE_NAME"
fi

# Write output environment file
log "Writing output to: $OUTPUT_FILE"
cat > "$OUTPUT_FILE" << EOF
CACHE_HIT=$CACHE_HIT
IMAGE_COMMIT=$FULL_COMMIT
IMAGE_TAG=$TAG
IMAGE_NAME=$IMAGE_NAME
IMAGE_REGISTRY=$IMAGE_REGISTRY
EOF

# Also output to stdout for easy capture
log "Results:"
log "  CACHE_HIT=$CACHE_HIT"
log "  IMAGE_COMMIT=$FULL_COMMIT"
log "  IMAGE_TAG=$TAG"
log "  IMAGE_NAME=$IMAGE_NAME"
log "  IMAGE_REGISTRY=$IMAGE_REGISTRY"

# Handle require-hit mode
if [[ "$REQUIRE_HIT" == "true" && "$CACHE_HIT" == "false" ]]; then
    error "Required image not found: $IMAGE_NAME"
    exit 1
fi

exit 0
