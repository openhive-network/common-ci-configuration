#! /bin/bash
# shellcheck source-path=SCRIPTDIR

SCRIPTPATH=$(realpath "$0")
SCRIPTDIR=$(dirname "$SCRIPTPATH")

export LOG_FILE=build_instance.log
source "$SCRIPTDIR/common.sh"

BUILD_IMAGE_TAG=""
REGISTRY=""
SRCROOTDIR=""
IMAGE_TAG_PREFIX=""
BUILD_HIVE_TESTNET=OFF
HIVE_CONVERTER_BUILD=OFF


print_help () {
    echo "Usage: $0 <image_tag> <src_dir> <registry_url> [OPTION[=VALUE]]..."
    echo
    echo "Allows to build docker image containing Hived installation"
    echo "OPTIONS:"
    echo "  --network-type=TYPE       Allows to specify type of blockchain network supported by built hived. Allowed values: mainnet, testnet, mirrornet"
    echo "  --export-binaries=PATH    Allows to specify a path where binaries shall be exported from built image."
    echo "  --help                    Display this help screen and exit"
    echo
}

EXPORT_PATH=""

while [ $# -gt 0 ]; do
  case "$1" in
    --network-type=*)
        type="${1#*=}"

        case $type in
          "testnet"*)
            BUILD_HIVE_TESTNET=ON
            IMAGE_TAG_PREFIX=testnet-
            ;;
          "mirrornet"*)
            BUILD_HIVE_TESTNET=OFF
            HIVE_CONVERTER_BUILD=ON
            IMAGE_TAG_PREFIX=mirror-
            ;;
          "mainnet"*)
            BUILD_HIVE_TESTNET=OFF
            HIVE_CONVERTER_BUILD=OFF
            IMAGE_TAG_PREFIX=
            ;;
           *)
            echo "ERROR: '$type' is not a valid network type"
            echo
            exit 3
        esac
        ;;
    --export-binaries=*)
        arg="${1#*=}"
        EXPORT_PATH="$arg"
        ;;
    --help)
        print_help
        exit 0
        ;;
    *)
        if [ -z "$BUILD_IMAGE_TAG" ];
        then
          BUILD_IMAGE_TAG="${1}"
        elif [ -z "$SRCROOTDIR" ];
        then
          SRCROOTDIR="${1}"
        elif [ -z "$REGISTRY" ];
        then
          REGISTRY=${1}
        else
          echo "ERROR: '$1' is not a valid option/positional argument"
          echo
          print_help
          exit 2
        fi
        ;;
    esac
    shift
done

[[ -z "$BUILD_IMAGE_TAG" ]] && echo "Missing argument #1: build image tag." && exit 1
[[ -z "$SRCROOTDIR" ]] && echo "Missing argument #2: source directory. Exiting." && exit 1
[[ -z "$REGISTRY" ]] && echo "Missing argument #3: target image registry. Exiting." && exit 1

# Supplement a registry path by trailing slash (if needed)
[[ "${REGISTRY}" != */ ]] && REGISTRY="${REGISTRY}/"

echo "Moving into source root directory: ${SRCROOTDIR}"
pushd "$SRCROOTDIR" || exit 1

export DOCKER_BUILDKIT=1

docker build --target=base_instance \
  --build-arg CI_REGISTRY_IMAGE="$REGISTRY" \
  --build-arg BUILD_HIVE_TESTNET=$BUILD_HIVE_TESTNET \
  --build-arg HIVE_CONVERTER_BUILD=$HIVE_CONVERTER_BUILD \
  --build-arg BUILD_IMAGE_TAG="$BUILD_IMAGE_TAG" \
  -t "${REGISTRY}base_instance:base_instance-${BUILD_IMAGE_TAG}" \
  -f Dockerfile .

# Build the image containing only binaries and be ready to start running hived instance, operating on mounted volumes pointing instance datadir and shm_dir
docker build --target=instance \
  --build-arg CI_REGISTRY_IMAGE="$REGISTRY" \
  --build-arg BUILD_HIVE_TESTNET=$BUILD_HIVE_TESTNET \
  --build-arg HIVE_CONVERTER_BUILD=$HIVE_CONVERTER_BUILD \
  --build-arg BUILD_IMAGE_TAG="$BUILD_IMAGE_TAG" \
  -t "${REGISTRY}${IMAGE_TAG_PREFIX}instance:instance-${BUILD_IMAGE_TAG}" \
  -f Dockerfile .

popd || exit 1

if [ -n "${EXPORT_PATH}" ] ;
then
  "$SCRIPTDIR/export-binaries.sh" "${REGISTRY}${IMAGE_TAG_PREFIX}instance:instance-${BUILD_IMAGE_TAG}" "${EXPORT_PATH}"
fi

