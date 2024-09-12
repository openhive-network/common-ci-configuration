#!/usr/bin/env bash
set -e

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

PROJECT_DIR="${1:?Missing arg #1 specifying a project source directory}"
REGISTRY_URL="${2:?Missing arg #2 pointing an NPM registry URL}"
SCOPE="${3:?Missing arg #3 pointing a package scope}"
PUBLISH_TOKEN="${4:?Missing arg #4 pointing a deployment token}"
NPM_PROVENANCE_ENABLE="${5:-0}"

NPM_FLAGS="--access=public"

echo> "${PROJECT_DIR}/.npmrc"

if [ "$REGISTRY_URL" != "registry.npmjs.org/" ]; then
  echo "${SCOPE}:registry=https://${REGISTRY_URL}" >> "${PROJECT_DIR}/.npmrc"
fi

echo "//${REGISTRY_URL}:_authToken=\"${PUBLISH_TOKEN}\"" >> "${PROJECT_DIR}/.npmrc"

pushd "${PROJECT_DIR}"

NAME=$(jq -r '.name' package.json)
VERSION=$(jq -r '.version' package.json)
PACKAGE_DIST_TAG=$(jq -r '.publishConfig.tag' package.json)

if [ "${VERSION}" = "" ]; then
  echo "Could not find a valid version name for package"
  exit 1
fi

NPM_FLAGS="${NPM_FLAGS} --tag \"${PACKAGE_DIST_TAG}\""

set +e

echo "Attempting to verify presence of package: ${NAME}@${VERSION}, dist-tag: ${PACKAGE_DIST_TAG} in the registry: ${REGISTRY_URL}"

if [ "${NPM_PROVENANCE_ENABLE}" -eq "1" ]; then
  NPM_FLAGS="${NPM_FLAGS} --provenance"
fi

# Check if package with given version has been already published
npm view "${NAME}@${VERSION}" version 2>/dev/null

if [ $? -eq 0 ]; then
  echo "Package already published"
else
  set -e
  echo "Publishing ${NAME}@${VERSION} to tag ${PACKAGE_DIST_TAG}"
  # We are going to repack the tarball as there are registry-dependent data in each job for package.json
  npm publish $NPM_FLAGS
fi

popd
