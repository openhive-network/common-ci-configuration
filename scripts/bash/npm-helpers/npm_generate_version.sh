#!/usr/bin/env bash

set -e

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

PROJECT_DIR="${1:?Missing arg #1 specifying a project source directory}"

REGISTRY_URL="${2:?Missing arg #2 pointing an NPM registry URL}"

SCOPE="${3:?Missing arg #3 pointing a package scope}"

PROJECT_NAME="${4:?Missing arg #4 pointing a project name}"

DIST_TAG=${5:-"dev"}

git config --global --add safe.directory '*'

git fetch --tags --quiet

pushd "${PROJECT_DIR}"

GIT_COMMIT_HASH=$(git rev-parse HEAD)
SHORT_HASH=$(git rev-parse --short HEAD)

GIT_COMMIT_TIME=$(TZ=UTC0 git show --quiet --date='format-local:%Y%m%d%H%M%S' --format="%cd")
TAG_TIME=${GIT_COMMIT_TIME:2}
_TAG=$(git tag --sort=-taggerdate | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+(-.+)?' | head -1)

echo "Read project original git tag: ${_TAG} (#${SHORT_HASH})"
# try to skip git tag project name suffix (useful for repositories where multiple targets are published, but sometimes they need to be tagged separately at git side)
TAG="${_TAG/\-${PROJECT_NAME}\-/}"

echo "Corrected tag (skipped subproject -${PROJECT_NAME}- suffix): ${TAG}"

echo "Preparing npm package for ${PROJECT_NAME}@${TAG} (#${SHORT_HASH})"

if [ "${TAG}" = "" ]; then
  echo "Could not find a valid tag name for branch"
  exit 1
fi

NEW_VERSION=""

if [ "${DIST_TAG}" = "latest" ]; then
  NEW_VERSION="${TAG}"
elif [ "$DIST_TAG" = "stable" ]; then
  NEW_VERSION="${TAG}-stable.${TAG_TIME}"
else
  DIST_TAG="dev"
  NEW_VERSION="${TAG}-${TAG_TIME}"
fi

git checkout "${PROJECT_DIR}/package.json" # be sure we're on clean version

jq ".name = \"${SCOPE}/${PROJECT_NAME}\" | .version = \"$NEW_VERSION\" | .publishConfig.registry = \"https://${REGISTRY_URL}\" | .publishConfig.tag = \"${DIST_TAG}\"" "${PROJECT_DIR}/package.json" > "${PROJECT_DIR}/package.json.tmp"

mv "${PROJECT_DIR}/package.json.tmp" "${PROJECT_DIR}/package.json"

# Display detailed publish config data
jq -r '.name + "@" + .version + " (" + .publishConfig.tag + ") " + .publishConfig.registry' "${PROJECT_DIR}/package.json"

 {
  echo BUILT_PACKAGE_NAME=${SCOPE}/${PROJECT_NAME}
  echo BUILT_PACKAGE_VERSION=${NEW_VERSION}
  echo BUILT_PACKAGE_DIST_TAG=${DIST_TAG}
  echo BUILT_PACKAGE_GIT_VERSION=${GIT_COMMIT_HASH}
} > "${PROJECT_DIR}/built_package_version_info.env"

echo "built_package_version_info.env file contents:"
cat "${PROJECT_DIR}/built_package_version_info.env"

popd
