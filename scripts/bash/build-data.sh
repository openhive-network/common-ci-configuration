#! /bin/bash
# shellcheck source-path=SCRIPTDIR

SCRIPTPATH=$(realpath "$0")
SCRIPTDIR=$(dirname "$SCRIPTPATH")

export LOG_FILE=build_data.log
source "$SCRIPTDIR/common.sh"

BUILD_IMAGE_TAG=${1:?"Missing argument #1: build image tag"}
shift
SRCROOTDIR=${1:?"Missing argument #2: source directory"}
shift
REGISTRY=${1:?"Missing argument #3: target image registry"}
shift 

# Supplement a registry path by trailing slash (if needed)
[[ "${REGISTRY}" != */ ]] && REGISTRY="${REGISTRY}/"

BLOCK_LOG_SUFFIX="-5m"

"$SCRIPTDIR/build-instance.sh" "${BUILD_IMAGE_TAG}" "${SRCROOTDIR}" "${REGISTRY}" "${BLOCK_LOG_SUFFIX}" "$@"

pushd "$SRCROOTDIR" ||exit 1 

docker build --target=data \
  --build-arg CI_REGISTRY_IMAGE="$REGISTRY" --build-arg BLOCK_LOG_SUFFIX="-5m" \
  --build-arg BUILD_IMAGE_TAG="$BUILD_IMAGE_TAG" -t "${REGISTRY}data:data-${BUILD_IMAGE_TAG}" -f Dockerfile .

popd || exit 1
