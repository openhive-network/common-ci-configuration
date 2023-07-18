#! /bin/bash
set -euo pipefail

SCRIPTSDIR="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
SRCDIR="${SCRIPTSDIR}/../../"

REGISTRY=${1:-"registry.gitlab.syncad.com/hive/common-ci-configuration/"}
EMSDK_VERSION=${1:-"3.1.43"}

export DOCKER_BUILDKIT=1

docker build --target=emscripten_builder \
  --build-arg EMSCRIPTEN_VERSION=${EMSDK_VERSION} \
  -t ${REGISTRY}emsdk:3.1.43 \
  -f "${SRCDIR}/Dockerfile.emscripten" "${SRCDIR}"
