#!/usr/bin/env bash
#
# Wait for a GitLab CI job of a given ref to finish and download its artifacts.
#
# Intended for GitHub Actions workflows that publish packages built by a GitLab
# pipeline (see docs/github-publishing.md). Such a workflow starts when a tag is
# mirrored to GitHub, usually long before the GitLab tag pipeline has produced
# the package. This script polls the GitLab API until a pipeline for the ref
# (optionally: for an expected commit SHA) exists and has finished successfully,
# then extracts the requested files out of the job's artifact archive.
#
# Usage:
#   gitlab-wait-for-artifact.sh --project <id|group%2Fname> --ref <ref> --job <name> \
#       --artifact '<path or glob inside the artifacts archive>' --output-dir <dir> \
#       [--gitlab-url <url>] [--expected-sha <sha>] [--wait-for pipeline|job] \
#       [--timeout <seconds>] [--poll-interval <seconds>] [--token <token>] [--output-env <file>]
#
# Options:
#   --project        GitLab project id or URL-encoded path (e.g. 452 or hive%2Fworkerbee)
#   --ref            Git ref the pipeline ran for (tag or branch name)
#   --job            Job name. Matrix jobs match by prefix: "build_wheel" also matches "build_wheel: [3.12]"
#   --artifact       Path or glob of the files to extract from the archive, e.g. 'dist/*.tgz' ("*" also matches "/")
#   --output-dir     Directory the matched files are extracted into (their archive paths are kept)
#   --gitlab-url     GitLab instance URL (default: https://gitlab.syncad.com)
#   --expected-sha   Only accept a pipeline for this commit (full or abbreviated SHA). Inside a git checkout an
#                    annotated tag's object SHA, which GitHub passes for a tag push, is peeled to its commit
#   --wait-for       pipeline (default): the whole pipeline must succeed; job: only the matched job(s)
#   --timeout        Give up after this many seconds (default: 14400)
#   --poll-interval  Seconds between polls (default: 60)
#   --token          GitLab private token for non-public projects (default: $GITLAB_TOKEN)
#   --output-env     File receiving KEY=VALUE lines: artifact_dir, artifact_files (space separated,
#                    relative to artifact_dir), pipeline_id, pipeline_url, pipeline_sha, job_ids.
#                    Pass "$GITHUB_OUTPUT" to expose them as step outputs.
#
# Exit codes:
#   0 files extracted   1 usage or API error   2 pipeline or job failed
#   3 timeout           4 artifacts missing, expired or not matching --artifact

set -euo pipefail

GITLAB_URL="https://gitlab.syncad.com"
PROJECT=""
REF=""
JOB=""
ARTIFACT=""
OUTPUT_DIR=""
EXPECTED_SHA=""
WAIT_FOR="pipeline"
TIMEOUT=14400
POLL_INTERVAL=60
TOKEN="${GITLAB_TOKEN:-}"
OUTPUT_ENV=""

usage() {
  sed -n '/^# Usage:/,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'
}

log() {
  printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"
}

die() {
  local code="$1"
  shift
  echo "ERROR: $*" >&2
  exit "$code"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gitlab-url) GITLAB_URL="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --job) JOB="$2"; shift 2 ;;
    --artifact) ARTIFACT="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --expected-sha) EXPECTED_SHA="$2"; shift 2 ;;
    --wait-for) WAIT_FOR="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --poll-interval) POLL_INTERVAL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --output-env) OUTPUT_ENV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die 1 "unknown option: $1" ;;
  esac
done

for required in PROJECT REF JOB ARTIFACT OUTPUT_DIR; do
  [[ -n "${!required}" ]] || { usage >&2; die 1 "--$(tr '[:upper:]_' '[:lower:]-' <<< "$required") is required"; }
done
[[ "$WAIT_FOR" == "pipeline" || "$WAIT_FOR" == "job" ]] || die 1 "--wait-for must be 'pipeline' or 'job', got '$WAIT_FOR'"
[[ "$TIMEOUT" =~ ^[0-9]+$ ]] || die 1 "--timeout must be a whole number of seconds, got '$TIMEOUT'"
[[ "$POLL_INTERVAL" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die 1 "--poll-interval must be a number of seconds, got '$POLL_INTERVAL'"
for tool in curl jq python3; do
  command -v "$tool" > /dev/null || die 1 "required tool not found: $tool"
done

# Inside a checkout, resolve the expected SHA through git: an annotated tag's object SHA (what GitHub passes
# for a tag push), a short SHA or a ref such as HEAD all become the full commit SHA.
if [[ -n "$EXPECTED_SHA" ]] && peeled="$(git rev-parse --verify --quiet "${EXPECTED_SHA}^{commit}" 2> /dev/null)" \
    && [[ "$peeled" != "$EXPECTED_SHA"* ]]; then
  echo "Expected SHA ${EXPECTED_SHA} resolved in the checkout to commit ${peeled}"
  EXPECTED_SHA="$peeled"
fi

API="${GITLAB_URL%/}/api/v4"
CURL_AUTH=()
if [[ -n "$TOKEN" ]]; then
  CURL_AUTH=(--header "PRIVATE-TOKEN: ${TOKEN}")
fi

# GET an API path (relative to /api/v4) and print the body. Fails on HTTP errors.
api_get() {
  curl -fsS --retry 3 --retry-delay 5 ${CURL_AUTH[@]+"${CURL_AUTH[@]}"} "${API}/$1"
}

# Newest pipeline of the ref (and of the expected commit, if given) as one JSON object, or nothing.
find_pipeline() {
  local ref_encoded
  ref_encoded="$(jq -rn --arg v "$REF" '$v | @uri')"
  api_get "projects/${PROJECT}/pipelines?ref=${ref_encoded}&per_page=50" \
    | jq -c --arg sha "$EXPECTED_SHA" '[.[] | select($sha == "" or (.sha | startswith($sha)))] | max_by(.id) // empty'
}

# All jobs of a pipeline (id, name, status, web_url) as one JSON array; follows pagination.
# Pages go through files: a big pipeline's job list does not fit into a command-line argument.
list_jobs() {
  local pipeline_id="$1" page=1 pages_dir
  pages_dir="$(mktemp -d)"
  while :; do
    api_get "projects/${PROJECT}/pipelines/${pipeline_id}/jobs?per_page=100&page=${page}" \
      | jq -c '[.[] | {id, name, status, web_url}]' > "${pages_dir}/page-${page}.json"
    [[ "$(jq 'length' "${pages_dir}/page-${page}.json")" -gt 0 ]] || break
    page=$((page + 1))
  done
  jq -c -s 'add // []' "${pages_dir}"/page-*.json
  rm -rf "$pages_dir"
}

# Jobs named exactly --job, or matrix instances of it ("name: [args]").
match_jobs() {
  jq -c --arg job "$JOB" '[.[] | select(.name == $job or (.name | startswith($job + ": [")))]'
}

describe_jobs() {
  jq -r '.[] | "  \(.name): \(.status) (\(.web_url))"'
}

# Extract the archive entries matching a glob into a directory and print their names (fnmatch:
# "*" also matches "/"). zipfile strips absolute paths and ".." components while extracting.
extract_matching() {
  python3 -I - "$1" "$2" "$3" <<'PY'
import fnmatch
import sys
import zipfile

archive, pattern, target = sys.argv[1:]
with zipfile.ZipFile(archive) as zip_file:
    names = [n for n in zip_file.namelist() if not n.endswith("/") and fnmatch.fnmatchcase(n, pattern)]
    zip_file.extractall(target, members=names)
print("\n".join(names))
PY
}

list_archive() {
  python3 -I - "$1" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as zip_file:
    print("\n".join(n for n in zip_file.namelist() if not n.endswith("/")))
PY
}

is_terminal_pipeline() {
  case "$1" in
    success|failed|canceled|skipped|manual) return 0 ;;
    *) return 1 ;;
  esac
}

deadline=$((SECONDS + TIMEOUT))
wait_or_timeout() {
  if (( SECONDS >= deadline )); then
    die 3 "timed out after ${TIMEOUT}s waiting for job '${JOB}' of ref '${REF}' in project ${PROJECT}"
  fi
  sleep "$POLL_INTERVAL"
}

log "Waiting for job '${JOB}' of ref '${REF}' in ${GITLAB_URL} project ${PROJECT}${EXPECTED_SHA:+ (commit ${EXPECTED_SHA})}, mode: ${WAIT_FOR}, timeout: ${TIMEOUT}s"

pipeline=""
matched=""
while :; do
  if ! pipeline="$(find_pipeline)"; then
    log "GitLab API request failed, retrying in ${POLL_INTERVAL}s"
    wait_or_timeout
    continue
  fi
  if [[ -z "$pipeline" ]]; then
    log "No pipeline for ref '${REF}'${EXPECTED_SHA:+ at commit ${EXPECTED_SHA}} yet"
    wait_or_timeout
    continue
  fi

  pipeline_id="$(jq -r '.id' <<< "$pipeline")"
  pipeline_status="$(jq -r '.status' <<< "$pipeline")"
  pipeline_sha="$(jq -r '.sha' <<< "$pipeline")"
  pipeline_url="$(jq -r '.web_url' <<< "$pipeline")"

  if ! jobs="$(list_jobs "$pipeline_id")"; then
    log "Listing jobs of pipeline ${pipeline_id} failed, retrying in ${POLL_INTERVAL}s"
    wait_or_timeout
    continue
  fi
  matched="$(match_jobs <<< "$jobs")"
  matched_count="$(jq 'length' <<< "$matched")"

  if [[ "$matched_count" -eq 0 ]]; then
    if is_terminal_pipeline "$pipeline_status"; then
      die 2 "pipeline ${pipeline_id} (${pipeline_status}) has no job named '${JOB}': ${pipeline_url}"
    fi
    log "Pipeline ${pipeline_id} is ${pipeline_status}, job '${JOB}' not created yet"
    wait_or_timeout
    continue
  fi

  not_run="$(jq -c '[.[] | select(.status == "failed" or .status == "canceled" or .status == "skipped" or .status == "manual")]' <<< "$matched")"
  if [[ "$(jq 'length' <<< "$not_run")" -gt 0 ]]; then
    echo "ERROR: job '${JOB}' did not succeed in pipeline ${pipeline_id} (${pipeline_url}):" >&2
    describe_jobs <<< "$not_run" >&2
    exit 2
  fi

  succeeded="$(jq '[.[] | select(.status == "success")] | length' <<< "$matched")"
  if [[ "$succeeded" -ne "$matched_count" ]]; then
    if is_terminal_pipeline "$pipeline_status"; then
      echo "ERROR: pipeline ${pipeline_id} is ${pipeline_status} but job '${JOB}' did not finish (${pipeline_url}):" >&2
      describe_jobs <<< "$matched" >&2
      exit 2
    fi
    log "Pipeline ${pipeline_id} is ${pipeline_status}; job '${JOB}': ${succeeded}/${matched_count} succeeded"
    wait_or_timeout
    continue
  fi

  if [[ "$WAIT_FOR" == "job" ]]; then
    break
  fi
  case "$pipeline_status" in
    success)
      break ;;
    manual)
      log "Pipeline ${pipeline_id} is blocked on a manual job; all automatic jobs passed"
      break ;;
    failed|canceled|skipped)
      die 2 "job '${JOB}' succeeded but pipeline ${pipeline_id} is ${pipeline_status}: ${pipeline_url}" ;;
    *)
      log "Job '${JOB}' succeeded, waiting for pipeline ${pipeline_id} (${pipeline_status}) to finish"
      wait_or_timeout
      continue ;;
  esac
done

log "Pipeline ${pipeline_id} (${pipeline_status}, commit ${pipeline_sha}) ${pipeline_url}"
describe_jobs <<< "$matched"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

job_ids=()
extracted=()
while IFS=$'\t' read -r job_id job_name; do
  job_ids+=("$job_id")
  archive="${tmp_dir}/job-${job_id}.zip"
  log "Downloading artifacts of job ${job_id} (${job_name})"
  if ! curl -fsS --retry 3 --retry-delay 5 ${CURL_AUTH[@]+"${CURL_AUTH[@]}"} -o "$archive" \
      "${API}/projects/${PROJECT}/jobs/${job_id}/artifacts"; then
    die 4 "artifacts of job ${job_id} (${job_name}) cannot be downloaded: they expired after the retention period or were never produced. Retry the GitLab job to regenerate them."
  fi
  if ! entries="$(extract_matching "$archive" "$ARTIFACT" "$OUTPUT_DIR")" || [[ -z "$entries" ]]; then
    echo "ERROR: nothing matching '${ARTIFACT}' in the artifacts of job ${job_id} (${job_name}). Archive contents:" >&2
    list_archive "$archive" >&2 || true
    exit 4
  fi
  while IFS= read -r entry; do
    extracted+=("$entry")
  done <<< "$entries"
done < <(jq -r '.[] | "\(.id)\t\(.name)"' <<< "$matched")

# Matrix jobs may carry the same file (e.g. a wheel inherited from a needed job); report each path once.
mapfile -t extracted < <(printf '%s\n' "${extracted[@]}" | awk '!seen[$0]++')

log "Extracted ${#extracted[@]} file(s) into ${OUTPUT_DIR}:"
printf '  %s\n' "${extracted[@]}"

if [[ -n "$OUTPUT_ENV" ]]; then
  {
    echo "artifact_dir=${OUTPUT_DIR}"
    echo "artifact_files=${extracted[*]}"
    echo "pipeline_id=${pipeline_id}"
    echo "pipeline_url=${pipeline_url}"
    echo "pipeline_sha=${pipeline_sha}"
    echo "job_ids=${job_ids[*]}"
  } >> "$OUTPUT_ENV"
fi
