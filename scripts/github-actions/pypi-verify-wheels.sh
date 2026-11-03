#!/usr/bin/env bash
#
# Check that the Python distributions downloaded from GitLab carry the version of the git tag.
#
# poetry-dynamic-versioning derives the version of a tag build from the tag, so a
# wheel built from tag "1.28.8-rc3" is version 1.28.8rc3 (PEP 440). Before the
# files go to PyPI this script applies the same mapping to the tag the GitHub
# workflow runs for and refuses distributions of any other version, which would
# mean the artifact was not built from the published tag.
#
# Usage:
#   pypi-verify-wheels.sh --dist-dir <dir> --tag <git tag> [--tag-strip-prefix <regex>]
#       [--expected-name <project name>] [--collect-dir <dir>] [--output-env <file>]
#
# Options:
#   --dist-dir          Directory holding the *.whl and *.tar.gz files (searched recursively)
#   --tag               Git tag, e.g. 1.28.8, 1.28.8-rc3, v1.2.0, beekeeper-v1.28.5rc1 (with --tag-strip-prefix)
#   --tag-strip-prefix  Extended regex removed from the start of the tag before mapping, e.g. 'beekeeper-v'
#   --expected-name     Fail if a distribution belongs to another project (compared after PEP 503 normalisation)
#   --collect-dir       Copy the verified files into this flat directory (what pypa/gh-action-pypi-publish uploads)
#   --output-env        File receiving KEY=VALUE lines: pypi_version, pypi_files (space separated, relative to
#                       --dist-dir), pypi_dist_dir (--collect-dir if given, else --dist-dir).
#                       Pass "$GITHUB_OUTPUT" to expose them as step outputs.

set -euo pipefail

DIST_DIR=""
TAG=""
TAG_STRIP_PREFIX=""
EXPECTED_NAME=""
COLLECT_DIR=""
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
    --dist-dir) DIST_DIR="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --tag-strip-prefix) TAG_STRIP_PREFIX="$2"; shift 2 ;;
    --expected-name) EXPECTED_NAME="$2"; shift 2 ;;
    --collect-dir) COLLECT_DIR="$2"; shift 2 ;;
    --output-env) OUTPUT_ENV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ -n "$DIST_DIR" && -n "$TAG" ]] || { usage >&2; die "--dist-dir and --tag are required"; }
[[ -d "$DIST_DIR" ]] || die "directory not found: $DIST_DIR"

# Map a git tag to the PEP 440 version poetry-dynamic-versioning produces for it:
# optional prefix and "v" are dropped, "-rc3" / "rc3" / "-rc.3" become "rc3", alpha/beta become a/b.
tag_to_pep440() {
  local tag="$1" base stage number
  if [[ -n "$TAG_STRIP_PREFIX" ]]; then
    tag="$(sed -E "s/^(${TAG_STRIP_PREFIX})//" <<< "$tag")"
  fi
  tag="${tag#v}"
  if [[ ! "$tag" =~ ^([0-9]+(\.[0-9]+)*)(-?([A-Za-z]+)\.?([0-9]*))?$ ]]; then
    return 1
  fi
  base="${BASH_REMATCH[1]}"
  stage="${BASH_REMATCH[4]}"
  number="${BASH_REMATCH[5]}"
  if [[ -z "$stage" ]]; then
    echo "$base"
    return 0
  fi
  case "${stage,,}" in
    a|alpha) stage="a" ;;
    b|beta) stage="b" ;;
    c|rc|pre|preview) stage="rc" ;;
    dev) stage=".dev" ;;
    post|rev|r) stage=".post" ;;
    *) return 1 ;;
  esac
  echo "${base}${stage}${number:-0}"
}

# PEP 503 project name normalisation, with "-" for the wheel/sdist file name form ("_").
normalize_name() {
  sed -E 's/[-_.]+/_/g' <<< "${1,,}"
}

expected_version="$(tag_to_pep440 "$TAG")" || die "tag '${TAG}' does not look like a release tag (expected e.g. 1.2.3, 1.2.3-rc1, v1.2.3; use --tag-strip-prefix for prefixed tags)"
expected_name=""
if [[ -n "$EXPECTED_NAME" ]]; then
  expected_name="$(normalize_name "$EXPECTED_NAME")"
fi
echo "Tag ${TAG} -> PEP 440 version ${expected_version}${EXPECTED_NAME:+, project ${EXPECTED_NAME}}"

DIST_DIR="$(cd "$DIST_DIR" && pwd -P)"
files=()
failures=0
while IFS= read -r file; do
  relative="${file#"${DIST_DIR}"/}"
  base="$(basename "$file")"
  if [[ "$base" == *.whl ]]; then
    # {name}-{version}(-{build})?-{python}-{abi}-{platform}.whl; name and version never contain "-"
    IFS=- read -r name version _ <<< "${base%.whl}"
  else
    # {name}-{version}.tar.gz; the version is everything after the last "-"
    stem="${base%.tar.gz}"
    name="${stem%-*}"
    version="${stem##*-}"
  fi
  if [[ "$version" != "$expected_version" ]]; then
    echo "ERROR: ${relative}: version ${version} does not match tag ${TAG} (${expected_version})" >&2
    failures=$((failures + 1))
  elif [[ -n "$expected_name" && "$(normalize_name "$name")" != "$expected_name" ]]; then
    echo "ERROR: ${relative}: project ${name} is not ${EXPECTED_NAME}" >&2
    failures=$((failures + 1))
  else
    echo "OK: ${relative}"
    files+=("$relative")
  fi
done < <(find "$DIST_DIR" -type f \( -name '*.whl' -o -name '*.tar.gz' \) | sort)

[[ "$failures" -eq 0 ]] || die "${failures} distribution(s) do not belong to tag ${TAG}"
[[ "${#files[@]}" -gt 0 ]] || die "no *.whl or *.tar.gz files found under ${DIST_DIR}"
echo "${#files[@]} distribution(s) of version ${expected_version} verified"

if [[ -n "$COLLECT_DIR" ]]; then
  mkdir -p "$COLLECT_DIR"
  COLLECT_DIR="$(cd "$COLLECT_DIR" && pwd -P)"
  for relative in "${files[@]}"; do
    cp -f "${DIST_DIR}/${relative}" "${COLLECT_DIR}/"
  done
  echo "Copied them into ${COLLECT_DIR}"
fi

if [[ -n "$OUTPUT_ENV" ]]; then
  {
    echo "pypi_version=${expected_version}"
    echo "pypi_files=${files[*]}"
    echo "pypi_dist_dir=${COLLECT_DIR:-${DIST_DIR}}"
  } >> "$OUTPUT_ENV"
fi
