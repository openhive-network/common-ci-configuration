#!/usr/bin/env bash
#
# Publish (or stage) an npm tarball at npmjs, idempotently, with trusted publishing.
#
# Meant to run in a GitHub Actions job that has `id-token: write`: npm >= 11.5.1
# then authenticates through OIDC trusted publishing and generates provenance by
# itself, so no token is needed or read. The tarball is unpacked into a clean
# directory and published from there with lifecycle scripts disabled, exactly as
# the GitLab staged-publishing job does (docs/npmjs-staged-publishing.md).
#
# A version that is already published (or, in stage mode, already staged) is
# skipped, so re-running the job is safe.
#
# Usage:
#   npm-publish-package.sh --tarball <file.tgz> [--mode publish|stage] [--access public|restricted]
#       [--dry-run [true|false]] [--output-env <file>]
#
# Options:
#   --tarball     Tarball to publish; name, version and dist-tag come from its package.json
#                 (publishConfig.tag, default latest; publishConfig.registry, default https://registry.npmjs.org/)
#   --mode        publish (default): the version goes live. stage: `npm stage publish`, a maintainer must
#                 approve it with 2FA (needs npm >= 11.15)
#   --access      npm --access value (default: public)
#   --dry-run     Run `npm publish --dry-run` instead of publishing or staging (an optional true/false value
#                 lets a workflow pass a boolean input through)
#   --output-env  File receiving KEY=VALUE lines: publish_status (published|staged|skipped|dry-run),
#                 package_name, package_version, package_dist_tag. Pass "$GITHUB_OUTPUT" for step outputs.

set -euo pipefail

TARBALL=""
MODE="publish"
ACCESS="public"
DRY_RUN="false"
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
    --mode) MODE="$2"; shift 2 ;;
    --access) ACCESS="$2"; shift 2 ;;
    --dry-run)
      if [[ "${2:-}" == "true" || "${2:-}" == "false" ]]; then
        DRY_RUN="$2"; shift 2
      else
        DRY_RUN="true"; shift
      fi ;;
    --output-env) OUTPUT_ENV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ -n "$TARBALL" ]] || { usage >&2; die "--tarball is required"; }
[[ -f "$TARBALL" ]] || die "tarball not found: $TARBALL"
[[ "$MODE" == "publish" || "$MODE" == "stage" ]] || die "--mode must be 'publish' or 'stage', got '$MODE'"
for tool in npm jq tar; do
  command -v "$tool" > /dev/null || die "required tool not found: $tool"
done

min_npm_version="11.5.1"   # OIDC trusted publishing
if [[ "$MODE" == "stage" ]]; then
  min_npm_version="11.15.0" # npm stage
fi
npm_version="$(npm --version)"
if [[ "$(printf '%s\n%s\n' "$min_npm_version" "$npm_version" | sort -V | head -n1)" != "$min_npm_version" ]]; then
  die "npm ${npm_version} is too old for --mode ${MODE}: npm >= ${min_npm_version} is required (use Node 24 or run 'npm install -g npm@latest')"
fi
if [[ -n "${NODE_AUTH_TOKEN:-}" ]]; then
  echo "WARNING: NODE_AUTH_TOKEN is set; npm will use it instead of OIDC trusted publishing. Do not set registry-url in actions/setup-node." >&2
fi

manifest="$(tar -xzOf "$TARBALL" package/package.json 2> /dev/null)" || die "not an npm package tarball (no package/package.json inside): $TARBALL"
package_name="$(jq -r '.name // empty' <<< "$manifest")"
package_version="$(jq -r '.version // empty' <<< "$manifest")"
dist_tag="$(jq -r '.publishConfig.tag // "latest"' <<< "$manifest")"
registry="$(jq -r '.publishConfig.registry // "https://registry.npmjs.org/"' <<< "$manifest")"
[[ -n "$package_name" && -n "$package_version" ]] || die "package.json in $TARBALL has no name or version"

status=""
echo "Package ${package_name}@${package_version}, dist-tag ${dist_tag}, registry ${registry}, npm ${npm_version}, mode ${MODE}"

# Published versions are public metadata: query without any user config so an auth problem cannot hide them.
if [[ "$(NPM_CONFIG_USERCONFIG=/dev/null npm view "${package_name}@${package_version}" version --registry "$registry" 2> /dev/null || true)" == "$package_version" ]]; then
  echo "${package_name}@${package_version} is already published at ${registry} - skipping"
  status="skipped"
elif [[ "$MODE" == "stage" && "$DRY_RUN" == "false" ]] \
    && staged_json="$(npm stage list "$package_name" --json --registry "$registry" 2> /dev/null)" \
    && [[ "$(jq --arg v "$package_version" '[.. | objects | select(.version? == $v)] | length' <<< "$staged_json" 2> /dev/null || echo 0)" != "0" ]]; then
  echo "${package_name}@${package_version} is already staged at ${registry} and awaits approval - skipping"
  status="skipped"
fi

if [[ -z "$status" ]]; then
  work_dir="$(mktemp -d)"
  trap 'rm -rf "$work_dir"' EXIT
  tar -xzf "$TARBALL" -C "$work_dir"
  cd "${work_dir}/package"

  # The tarball holds the exact content to publish; --ignore-scripts keeps prepack/prepublishOnly from re-running.
  npm_args=(--ignore-scripts --access "$ACCESS" --tag "$dist_tag" --registry "$registry")
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run: npm publish --dry-run ${npm_args[*]}"
    npm publish --dry-run "${npm_args[@]}"
    status="dry-run"
  elif [[ "$MODE" == "stage" ]]; then
    echo "Staging ${package_name}@${package_version}"
    npm stage publish "${npm_args[@]}"
    echo "Staged. A maintainer must approve it with 2FA: npm stage list ${package_name}; npm stage approve <stage-id> --otp <code>"
    status="staged"
  else
    echo "Publishing ${package_name}@${package_version}"
    npm publish "${npm_args[@]}"
    status="published"
  fi
fi

echo "Result: ${status}"
if [[ -n "$OUTPUT_ENV" ]]; then
  {
    echo "publish_status=${status}"
    echo "package_name=${package_name}"
    echo "package_version=${package_version}"
    echo "package_dist_tag=${dist_tag}"
  } >> "$OUTPUT_ENV"
fi
