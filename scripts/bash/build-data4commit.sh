#! /bin/bash
# shellcheck source-path=SCRIPTDIR

SCRIPTPATH=$(realpath "$0")
SCRIPTDIR=$(dirname "$SCRIPTPATH")

export LOG_FILE=build_data4commit.log
source "$SCRIPTDIR/common.sh"

echo -e "\e[0Ksection_start:$(date +%s):input_processing[collapsed=true]\r\e[0KChecking build-data4commit.sh script input..."
COMMIT=${1:?"Missing argument #1: commit SHA"}
shift
REGISTRY=${1:?"Missing argument #2: target image registry"}
shift
REPOSITORY=${1:?"Missing argument #3: repository URL"}
shift
BRANCH="master"

BUILD_IMAGE_TAG=$COMMIT

readarray -d "/" -t REPOSITORY_URL_ARRAY <<< "$REPOSITORY"
REPOSITORY_ARRAY_LENGTH=${#REPOSITORY_URL_ARRAY[*]}
readarray -d "." -t SPLIT_REPOSITORY_NAME <<< "${REPOSITORY_URL_ARRAY[$REPOSITORY_ARRAY_LENGTH-1]}"
PROJECT_NAME=${SPLIT_REPOSITORY_NAME[0]}
echo -e "\e[0Ksection_end:$(date +%s):input_processing\r\e[0K"

echo -e "\e[0Ksection_start:$(date +%s):cloning[collapsed=true]\r\e[0KCloning repository $REPOSITORY into directory ${PROJECT_NAME}-${COMMIT}..."
do_clone "$BRANCH" "./${PROJECT_NAME}-${COMMIT}" "$REPOSITORY" "$COMMIT"
echo -e "\e[0Ksection_end:$(date +%s):cloning\r\e[0K"

"$SCRIPTDIR/build-data.sh" "$BUILD_IMAGE_TAG" "./${PROJECT_NAME}-${COMMIT}" "$REGISTRY" "$@"

