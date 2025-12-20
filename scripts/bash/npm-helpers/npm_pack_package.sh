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

# warning pnpm prints additional (non json) lines referencing prepack actions done while packing. They start from `>` and must be filtered out before processing by jq
pnpm pack --pack-destination "${OUTPUT_DIR}" --json | grep -v '^>.*$' > "${OUTPUT_DIR}/built_package_info.json"
BUILT_PACKAGE_NAME=$(jq -r .filename "${OUTPUT_DIR}/built_package_info.json")
# Extract just the filename for cross-runner compatibility
BUILT_PACKAGE_FILENAME=$(basename "${BUILT_PACKAGE_NAME}")
# Compute relative path from CI_PROJECT_DIR (strip CI_PROJECT_DIR prefix and leading slash)
BUILT_PACKAGE_RELPATH="${OUTPUT_DIR#${CI_PROJECT_DIR}/}/${BUILT_PACKAGE_FILENAME}"
{
  echo PACKAGE_SOURCE_DIR="${SOURCE_DIR}"
  echo BUILT_PACKAGE_PATH="${BUILT_PACKAGE_NAME}"
  echo BUILT_PACKAGE_FILENAME="${BUILT_PACKAGE_FILENAME}"
  echo BUILT_PACKAGE_RELPATH="${BUILT_PACKAGE_RELPATH}"
} > "${SOURCE_DIR}/built_package_info.env"

echo "built_package_info.env file contents:"
cat "${SOURCE_DIR}/built_package_info.env"

popd
