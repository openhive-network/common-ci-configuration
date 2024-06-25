#!/usr/bin/env bash

set -e

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

PROJECT_DIR="${1:?Missing arg #1 specifying a project source directory}"

REGISTRY_URL="${2:?Missing arg #2 pointing an NPM registry URL}"

SCOPE="${3:?Missing arg #3 pointing a package scope}"

PROJECT_NAME="${4:?Missing arg #4 pointing a project name}"

git config --global --add safe.directory '*'

git fetch --tags --quiet

pushd "${PROJECT_DIR}"

GIT_COMMIT_HASH=$(git rev-parse HEAD)
SHORT_HASH=$(git rev-parse --short HEAD)

# warning: same commit can be referenced from multiple branches. It often happens between main/master and develop branches. Let's make a priority for main/master
CURRENT_BRANCH_IMPL=$(git branch -r --contains "${SHORT_HASH}" --list origin/master --list origin/main)

if [ "${CURRENT_BRANCH_IMPL}" = "" ]; then
  CURRENT_BRANCH_IMPL=$(git branch -r --contains "${SHORT_HASH}" --list origin/develop)
fi

if [ "${CURRENT_BRANCH_IMPL}" = "" ]; then
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
else
  CURRENT_BRANCH="${CURRENT_BRANCH_IMPL#*/}"
fi

GIT_COMMIT_TIME=$(TZ=UTC0 git show --quiet --date='format-local:%Y%m%d%H%M%S' --format="%cd")
TAG_TIME=${GIT_COMMIT_TIME:2}
TAG=$(git tag --sort=-taggerdate | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+(-.+)?' | head -1)

echo "Preparing npm packge for ${CURRENT_BRANCH}@${TAG} (#${SHORT_HASH})"

if [ "${TAG}" = "" ]; then
  echo "Could not find a valid tag name for branch"
  exit 1
fi

DIST_TAG=""
NEW_VERSION=""

if [[ "$CURRENT_BRANCH" = "master" ]] || [[ "$CURRENT_BRANCH" = "main" ]]; then
  DIST_TAG="latest"
  NEW_VERSION="${TAG}"
elif [ "$CURRENT_BRANCH" = "develop" ]; then
  DIST_TAG="stable"
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
