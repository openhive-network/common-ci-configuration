#!/usr/bin/env bash
#
# Rewrite a GitLab-built npm tarball so it can be published to npmjs from GitHub.
#
# The build jobs of templates/npm_projects.gitlab-ci.yml set package.json up for
# the GitLab package registry: `publishConfig.registry` points at GitLab and
# `repository.url` at the GitLab project. npm provenance refuses to publish when
# `repository.url` does not match the repository the workflow runs in, so both
# fields are rewritten here. Nothing else in the tarball changes, and no
# lifecycle script runs: the package is unpacked, patched with jq and packed again.
#
# Usage:
#   npm-rewrite-package.sh --tarball <file.tgz> --output-dir <dir> --repository-url <url> \
#       [--repository-directory <subdir>] [--registry <url>] [--dist-tag <tag>] \
#       [--expected-version <version> | --tag <git tag> [--tag-subproject <name>]] [--output-env <file>]
#
# Options:
#   --tarball               Tarball produced by `pnpm pack` / `npm pack` (contains package/package.json)
#   --output-dir            Directory the rewritten tarball is written to (same file name)
#   --repository-url        Repository the GitHub workflow runs in, e.g. https://github.com/openhive-network/workerbee
#   --repository-directory  Package directory inside that repository, for monorepo packages (sets repository.directory)
#   --registry              Target registry for publishConfig.registry (default: https://registry.npmjs.org/)
#   --dist-tag              Dist-tag for publishConfig.tag (default: latest, the channel of protected tags)
#   --expected-version      Fail unless package.json has exactly this version
#   --tag                   Derive --expected-version from a git tag, like npm_generate_version.sh does
#   --tag-subproject        Subproject name whose "-<name>-" infix is dropped from --tag (multi-package repositories)
#   --output-env            File receiving KEY=VALUE lines: package_name, package_version, package_dist_tag,
#                           package_tarball. Pass "$GITHUB_OUTPUT" to expose them as step outputs.

set -euo pipefail

TARBALL=""
OUTPUT_DIR=""
REPOSITORY_URL=""
REPOSITORY_DIRECTORY=""
REGISTRY="https://registry.npmjs.org/"
DIST_TAG="latest"
EXPECTED_VERSION=""
TAG=""
TAG_SUBPROJECT=""
OUTPUT_ENV=""

usage() {
  sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tarball) TARBALL="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --repository-url) REPOSITORY_URL="$2"; shift 2 ;;
    --repository-directory) REPOSITORY_DIRECTORY="$2"; shift 2 ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    --dist-tag) DIST_TAG="$2"; shift 2 ;;
    --expected-version) EXPECTED_VERSION="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --tag-subproject) TAG_SUBPROJECT="$2"; shift 2 ;;
    --output-env) OUTPUT_ENV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ -n "$TARBALL" && -n "$OUTPUT_DIR" && -n "$REPOSITORY_URL" ]] || { usage >&2; die "--tarball, --output-dir and --repository-url are required"; }
[[ -f "$TARBALL" ]] || die "tarball not found: $TARBALL"
command -v jq > /dev/null || die "required tool not found: jq"

if [[ -n "$TAG" ]]; then
  [[ -z "$EXPECTED_VERSION" ]] || die "--expected-version and --tag are mutually exclusive"
  EXPECTED_VERSION="$TAG"
  if [[ -n "$TAG_SUBPROJECT" ]]; then
    # Same transformation as npm_generate_version.sh: a tag "1.2.3-wax-rc1" names version "1.2.3rc1" of subproject wax
    EXPECTED_VERSION="${TAG/-${TAG_SUBPROJECT}-/}"
  fi
fi

# npm compares repository URLs after normalisation; "git+https://host/owner/repo.git" is the form npm itself writes.
repository_url="${REPOSITORY_URL#git+}"
repository_url="${repository_url%/}"
repository_url="${repository_url%.git}"
repository_url="git+${repository_url}.git"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

tar -xzf "$TARBALL" -C "$work_dir"
package_json="${work_dir}/package/package.json"
[[ -f "$package_json" ]] || die "not an npm package tarball (no package/package.json inside): $TARBALL"

package_name="$(jq -r '.name // empty' "$package_json")"
package_version="$(jq -r '.version // empty' "$package_json")"
[[ -n "$package_name" && -n "$package_version" ]] || die "package.json in $TARBALL has no name or version"
if [[ -n "$EXPECTED_VERSION" && "$package_version" != "$EXPECTED_VERSION" ]]; then
  die "${package_name} in ${TARBALL} has version ${package_version}, expected ${EXPECTED_VERSION}. The tarball was not built from the published tag."
fi

echo "Rewriting ${package_name}@${package_version} for ${REGISTRY} (dist-tag ${DIST_TAG}), repository ${repository_url}${REPOSITORY_DIRECTORY:+ directory ${REPOSITORY_DIRECTORY}}"

patched="${work_dir}/package.json.patched"
jq --arg url "$repository_url" --arg dir "$REPOSITORY_DIRECTORY" --arg registry "$REGISTRY" --arg tag "$DIST_TAG" '
  .repository = ({type: "git", url: $url} + (if $dir == "" then {} else {directory: $dir} end))
  | .publishConfig = ((.publishConfig // {}) + {registry: $registry, tag: $tag})
' "$package_json" > "$patched"
echo "package.json changes:"
diff <(jq -S . "$package_json") <(jq -S . "$patched") || true
mv "$patched" "$package_json"

if [[ -e "${work_dir}/package/.npmrc" ]]; then
  echo "Dropping .npmrc shipped inside the package"
  rm -f "${work_dir}/package/.npmrc"
fi

mkdir -p "$OUTPUT_DIR"
output_tarball="$(cd "$OUTPUT_DIR" && pwd -P)/$(basename "$TARBALL")"
tar -czf "$output_tarball" --owner=0 --group=0 --numeric-owner -C "$work_dir" package
echo "Wrote ${output_tarball}"

if [[ -n "$OUTPUT_ENV" ]]; then
  {
    echo "package_name=${package_name}"
    echo "package_version=${package_version}"
    echo "package_dist_tag=${DIST_TAG}"
    echo "package_tarball=${output_tarball}"
  } >> "$OUTPUT_ENV"
fi
