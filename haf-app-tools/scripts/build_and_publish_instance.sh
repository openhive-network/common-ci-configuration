#!/bin/bash
#
# Build and publish HAF app Docker images
# Generic wrapper that calls repo's build_instance.sh and handles registry publishing
#
# Usage from repo CI:
#   /path/to/common-ci-configuration/haf-app-tools/scripts/build_and_publish_instance.sh \
#     --image-tag=$CI_COMMIT_SHORT_SHA \
#     --src-dir=$CI_PROJECT_DIR \
#     --project-name=$CI_PROJECT_NAME
#

set -euo pipefail

print_help() {
cat <<EOF
Usage: $0 --image-tag=TAG --src-dir=DIR [OPTIONS]

Build HAF app Docker images and push to registries.

OPTIONS:
  --image-tag=TAG           Tag for the Docker images (required)
  --src-dir=DIR             Source directory containing build_instance.sh (required)
  --project-name=NAME       Project name for Docker Hub (default: \$CI_PROJECT_NAME)
  --registry=URL            Registry URL (default: \$CI_REGISTRY_IMAGE)
  --docker-hub-user=USER    Docker Hub username (optional, default: \$DOCKER_HUB_USER)
  --docker-hub-password=PW  Docker Hub password (optional, default: \$DOCKER_HUB_PASSWORD)
  --help                    Show this help

This script calls the repo's scripts/ci-helpers/build_instance.sh to build images,
then optionally pushes to Docker Hub if credentials are provided.
EOF
}

IMAGE_TAG=""
SRC_DIR=""
PROJECT_NAME="${CI_PROJECT_NAME:-}"
REGISTRY="${CI_REGISTRY_IMAGE:-}"
DOCKER_HUB_USER="${DOCKER_HUB_USER:-}"
DOCKER_HUB_PASSWORD="${DOCKER_HUB_PASSWORD:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image-tag=*)
            IMAGE_TAG="${1#*=}"
            ;;
        --src-dir=*)
            SRC_DIR="${1#*=}"
            ;;
        --project-name=*)
            PROJECT_NAME="${1#*=}"
            ;;
        --registry=*)
            REGISTRY="${1#*=}"
            ;;
        --docker-hub-user=*)
            DOCKER_HUB_USER="${1#*=}"
            ;;
        --docker-hub-password=*)
            DOCKER_HUB_PASSWORD="${1#*=}"
            ;;
        --help|-h|-?)
            print_help
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1"
            print_help
            exit 1
            ;;
    esac
    shift
done

if [[ -z "$IMAGE_TAG" ]]; then
    echo "ERROR: --image-tag is required"
    print_help
    exit 1
fi

if [[ -z "$SRC_DIR" ]]; then
    echo "ERROR: --src-dir is required"
    print_help
    exit 1
fi

if [[ -z "$REGISTRY" ]]; then
    echo "ERROR: --registry or CI_REGISTRY_IMAGE must be set"
    exit 1
fi

if [[ -z "$PROJECT_NAME" ]]; then
    echo "ERROR: --project-name or CI_PROJECT_NAME must be set"
    exit 1
fi

BUILD_SCRIPT="$SRC_DIR/scripts/ci-helpers/build_instance.sh"
if [[ ! -x "$BUILD_SCRIPT" ]]; then
    echo "ERROR: Build script not found or not executable: $BUILD_SCRIPT"
    exit 1
fi

echo "Building $PROJECT_NAME images with tag: $IMAGE_TAG"
echo "Registry: $REGISTRY"
echo "Source dir: $SRC_DIR"

# Call repo's build_instance.sh to build and push to GitLab registry
"$BUILD_SCRIPT" "$IMAGE_TAG" "$SRC_DIR" "$REGISTRY"

# Push to Docker Hub if credentials provided
if [[ -n "$DOCKER_HUB_USER" && -n "$DOCKER_HUB_PASSWORD" ]]; then
    echo "Pushing to Docker Hub..."
    echo "$DOCKER_HUB_PASSWORD" | docker login -u "$DOCKER_HUB_USER" --password-stdin

    DOCKER_HUB_IMAGE="hiveio/${PROJECT_NAME}:$IMAGE_TAG"
    docker tag "$REGISTRY:$IMAGE_TAG" "$DOCKER_HUB_IMAGE"
    docker push "$DOCKER_HUB_IMAGE"
    echo "Pushed: $DOCKER_HUB_IMAGE"
fi

echo "Build and publish completed successfully"
