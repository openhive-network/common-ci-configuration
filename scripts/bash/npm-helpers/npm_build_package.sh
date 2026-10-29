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

# --recursive leaves the workspace root out. A single package whose workspace has
# no other project (ccc's symlinked pnpm-workspace.yaml globs match none) is then
# built only with --include-workspace-root; a root next to workspace packages
# stays out, as its build script usually builds them itself.
build_flags=(--recursive --workspace-concurrency=1)
if [ "$(pnpm pkg get scripts.build)" != "{}" ] && [ "$(pnpm list --recursive --depth -1 --parseable | wc -l)" -le 1 ]; then
  build_flags+=(--include-workspace-root)
fi
pnpm "${build_flags[@]}" run build

"${SCRIPTPATH}/npm_pack_package.sh" "${SOURCE_DIR}" "${REGISTRY_URL}" "${SCOPE}" "${PROJECT_NAME}" "${OUTPUT_DIR}" "${COMMIT_REF_PROTECTED}" "${COMMIT_TAG}"

popd
