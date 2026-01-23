#!/bin/bash
#
# verify_submodule_ref.sh - Verify that GitLab CI include ref matches submodule commit
#
# This script checks if the ref specified in a GitLab CI include directive
# matches the actual commit of the corresponding submodule.
#
# Usage:
#   ./verify_submodule_ref.sh <submodule_path> <gitlab_ci_file> <project_pattern>
#
# Arguments:
#   submodule_path    Path to the submodule directory (e.g., "hive")
#   gitlab_ci_file    Path to the GitLab CI file (e.g., ".gitlab-ci.yml")
#   project_pattern   Project pattern to search for (e.g., "hive/hive")
#
# Example:
#   ./verify_submodule_ref.sh hive .gitlab-ci.yml "hive/hive"
#
# Exit codes:
#   0 - Refs match
#   1 - Refs do not match or error occurred
#

set -euo pipefail

# Colors for output
TXT_GREEN="${TXT_GREEN:-\e[1;32m}"
TXT_RED="${TXT_RED:-\e[1;31m}"
TXT_CLEAR="${TXT_CLEAR:-\e[0m}"

log_error() { echo -e "${TXT_RED}ERROR: $*${TXT_CLEAR}" >&2; }
log_success() { echo -e "${TXT_GREEN}$*${TXT_CLEAR}"; }

show_help() {
    head -25 "$0" | grep -E '^#' | sed 's/^# \?//'
    exit 0
}

# Extracts the ref value for a given project from GitLab CI file.
# Arguments: $1 - gitlab_ci_file, $2 - project_pattern
# Output: ref value (commit hash)
extract_include_ref() {
    local gitlab_ci_file="$1"
    local project_pattern="$2"

    # Escape slashes for awk regex
    local escaped_pattern="${project_pattern//\//\\/}"

    # Find line with project pattern, get next line with ref
    awk "/project:.*${escaped_pattern}/{getline; print}" "${gitlab_ci_file}" \
        | awk -F'ref:' '{print $2}' \
        | tr -d " '"
}

# Gets the current commit hash of a submodule.
# Arguments: $1 - submodule_path
# Output: commit hash
get_submodule_commit() {
    local submodule_path="$1"
    git -C "${submodule_path}" rev-parse HEAD
}

main() {
    if [[ $# -lt 3 ]] || [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
        show_help
    fi

    local submodule_path="$1"
    local gitlab_ci_file="$2"
    local project_pattern="$3"

    # Validate inputs
    if [[ ! -d "${submodule_path}" ]]; then
        log_error "Submodule path does not exist: ${submodule_path}"
        exit 1
    fi

    if [[ ! -f "${gitlab_ci_file}" ]]; then
        log_error "GitLab CI file does not exist: ${gitlab_ci_file}"
        exit 1
    fi

    # Extract refs
    local include_ref
    local submodule_commit

    include_ref=$(extract_include_ref "${gitlab_ci_file}" "${project_pattern}")
    submodule_commit=$(get_submodule_commit "${submodule_path}")

    if [[ -z "${include_ref}" ]]; then
        log_error "Could not find include ref for project '${project_pattern}' in ${gitlab_ci_file}"
        exit 1
    fi

    echo "Include ref:      ${include_ref}"
    echo "Submodule commit: ${submodule_commit}"

    # Compare refs
    if [[ "${include_ref}" != "${submodule_commit}" ]]; then
        log_error "${project_pattern} include ref does not match submodule commit"
        exit 1
    fi

    log_success "OK: refs match"
}

main "$@"
