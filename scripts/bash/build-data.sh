#! /bin/bash
# shellcheck source-path=SCRIPTDIR

SCRIPTPATH=$(realpath "$0")
SCRIPTDIR=$(dirname "$SCRIPTPATH")

export LOG_FILE=build_data.log
source "$SCRIPTDIR/common.sh"

echo -e "\e[0Ksection_start:$(date +%s):input_processing[collapsed=true]\r\e[0KChecking build-data.sh script input..."
BUILD_IMAGE_TAG=${1:?"Missing argument #1: build image tag"}
shift
SRCROOTDIR=${1:?"Missing argument #2: source directory"}
shift
REGISTRY=${1:?"Missing argument #3: target image registry"}
shift 

# Supplement a registry path by trailing slash (if needed)
[[ "${REGISTRY}" != */ ]] && REGISTRY="${REGISTRY}/"

BUILD_HIVE_TESTNET=OFF
HIVE_CONVERTER_BUILD=OFF
echo -e "\e[0Ksection_end:$(date +%s):input_processing\r\e[0K"

"$SCRIPTDIR/build-instance.sh" "${BUILD_IMAGE_TAG}" "${SRCROOTDIR}" "${REGISTRY}" "$@"

echo -e "\e[0Ksection_start:$(date +%s):data_image_docker_build[collapsed=true]\r\e[0KBuilding data image..."
echo "Instance image built. Attempting to build a data image based on it..."

pushd "$SRCROOTDIR" ||exit 1 

docker build --target=data \
  --build-arg CI_REGISTRY_IMAGE="$REGISTRY" \
  --build-arg BUILD_HIVE_TESTNET=$BUILD_HIVE_TESTNET \
  --build-arg HIVE_CONVERTER_BUILD=$HIVE_CONVERTER_BUILD \
  --build-arg BUILD_IMAGE_TAG="$BUILD_IMAGE_TAG" \
  -t "${REGISTRY}data:data-${BUILD_IMAGE_TAG}" -f Dockerfile .

popd || exit 1
echo -e "\e[0Ksection_end:$(date +%s):data_image_docker_build\r\e[0K"