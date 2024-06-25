#! /bin/bash
set -euo pipefail

SCRIPTSDIR="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
SRCDIR="${SCRIPTSDIR}/../../"

REGISTRY=${1:-"registry.gitlab.syncad.com/hive/common-ci-configuration/"}
EMSDK_VERSION=${1:-"3.1.56"}

export DOCKER_BUILDKIT=1

docker build --target=supplemented_emscripten_builder \
  --build-arg "EMSCRIPTEN_VERSION=${EMSDK_VERSION}" \
  --tag "${REGISTRY}emsdk:${EMSDK_VERSION}-5" \
  --file "${SRCDIR}/Dockerfile.emscripten" "${SRCDIR}"
