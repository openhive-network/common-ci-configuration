#!/usr/bin/env bash
set -e

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

SOURCE_DIR="${1:?Missing arg #1 specifying a project source directory}"
REGISTRY_URL="${2:?Missing arg #2 pointing an NPM registry URL}"
SCOPE="${3:?Missing arg #3 pointing a package scope}"
PROJECT_NAME="${4:?Missing arg #4 pointing a project name}"
OUTPUT_DIR="${5:?Missing arg #5 pointing an output directory}"
COMMIT_REF_PROTECTED="${6:-}"
COMMIT_TAG="${7:-}"

pushd "${SOURCE_DIR}" # move to the project directory (where package.json file is located)

"${SCRIPTPATH}/npm_generate_version.sh" "${SOURCE_DIR}" "${REGISTRY_URL}" "${SCOPE}" "${PROJECT_NAME}" "${COMMIT_REF_PROTECTED}" "${COMMIT_TAG}"

npm pack --pack-destination "${OUTPUT_DIR}" --json > "${OUTPUT_DIR}/built_package_info.json"
BUILT_PACKAGE_NAME=$(jq -r .[].filename "${OUTPUT_DIR}/built_package_info.json")
{
  echo PACKAGE_SOURCE_DIR="${SOURCE_DIR}"
  echo BUILT_PACKAGE_PATH="${OUTPUT_DIR}/${BUILT_PACKAGE_NAME}"
} > "${SOURCE_DIR}/built_package_info.env"

echo "built_package_info.env file contents:"
cat "${SOURCE_DIR}/built_package_info.env"

popd
