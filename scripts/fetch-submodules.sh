#!/bin/bash
# fetch-submodules.sh - Fetch or clone specific submodules with cache support
#
# This script handles submodule initialization with fetch-or-clone logic,
# reducing GitLab server load by:
# 1. Reusing existing workspace checkouts between jobs
# 2. Fetching only the specific commit needed (not full history)
# 3. Running submodule fetches in parallel (optional)
#
# Usage: fetch-submodules.sh [--parallel] <path1> [path2] ...
#
# Examples:
#   fetch-submodules.sh tests/python/hive-local-tools/test-tools
#   fetch-submodules.sh --parallel path/to/sub1 path/to/sub2

set -euo pipefail

PARALLEL=false
PATHS=()

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --parallel) PARALLEL=true; shift ;;
        --help|-h)
            echo "Usage: fetch-submodules.sh [--parallel] <path1> [path2] ..."
            echo ""
            echo "Fetch or clone specific submodules with cache support."
            echo ""
            echo "Options:"
            echo "  --parallel    Fetch submodules in parallel"
            echo "  --help, -h    Show this help message"
            echo ""
            echo "Examples:"
            echo "  fetch-submodules.sh tests/python/hive-local-tools/test-tools"
            echo "  fetch-submodules.sh --parallel path/to/sub1 path/to/sub2"
            exit 0
            ;;
        *) PATHS+=("$1"); shift ;;
    esac
done

if [[ ${#PATHS[@]} -eq 0 ]]; then
    echo "ERROR: No submodule paths specified"
    echo "Usage: fetch-submodules.sh [--parallel] <path1> [path2] ..."
    exit 1
fi

# Git 2.36+ refuses to work in directories not owned by current user.
# In CI, the workspace is often owned by a different user (e.g., root vs gitlab-runner).
# This must be set before any git commands run.
git config --global --add safe.directory '*' 2>/dev/null || true

# Get submodule commit from main repo
get_submodule_ref() {
    local path="$1"
    git ls-tree HEAD "$path" 2>/dev/null | awk '{print $3}'
}

# Get submodule URL from .gitmodules
get_submodule_url() {
    local path="$1"
    local submodule_name=""
    local relative_url=""

    # Find the submodule name for this path by searching .gitmodules
    while IFS= read -r line; do
        if [[ "$line" =~ ^\[submodule\ \"(.+)\"\]$ ]]; then
            submodule_name="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^[[:space:]]*path[[:space:]]*=[[:space:]]*(.+)$ ]]; then
            local found_path="${BASH_REMATCH[1]}"
            found_path="${found_path## }"  # trim leading space
            found_path="${found_path%% }"  # trim trailing space
            if [[ "$found_path" == "$path" ]]; then
                # Found the right submodule, now get its URL
                relative_url=$(git config --file .gitmodules --get "submodule.${submodule_name}.url" 2>/dev/null || echo "")
                break
            fi
        fi
    done < .gitmodules

    if [[ -z "$relative_url" ]]; then
        echo ""
        return
    fi

    # Resolve relative URL to absolute
    if [[ "$relative_url" == ../* ]]; then
        if [[ -n "${CI_SERVER_URL:-}" ]]; then
            # In GitLab CI, use CI_SERVER_URL
            local repo_name="${relative_url#../}"
            repo_name="${repo_name%.git}"
            echo "${CI_SERVER_URL}/hive/${repo_name}.git"
        else
            # Fall back to resolving from git remote
            local origin_url
            origin_url=$(git remote get-url origin 2>/dev/null || echo "")
            if [[ -n "$origin_url" ]]; then
                local base_url="${origin_url%/*}"
                local repo_name="${relative_url#../}"
                echo "${base_url}/${repo_name}"
            else
                echo "$relative_url"
            fi
        fi
    else
        echo "$relative_url"
    fi
}

# Fetch or clone a single submodule
fetch_or_clone_submodule() {
    local path="$1"
    local ref url

    ref=$(get_submodule_ref "$path")
    if [[ -z "$ref" ]]; then
        echo "[$path] Warning: not a submodule in current commit"
        return 0
    fi

    url=$(get_submodule_url "$path")
    if [[ -z "$url" ]]; then
        echo "[$path] ERROR: Could not determine URL from .gitmodules"
        return 1
    fi

    echo "[$path] Target: $ref"
    echo "[$path] URL: $url"

    if [[ -d "$path/.git" ]] || [[ -f "$path/.git" ]]; then
        echo "[$path] Fetching existing checkout..."
        # Try shallow fetch of specific ref first, fall back to full fetch
        if ! git -C "$path" fetch origin --depth=1 "$ref" 2>/dev/null; then
            echo "[$path] Shallow fetch of ref failed, trying regular fetch..."
            git -C "$path" fetch origin || {
                echo "[$path] Fetch failed, re-cloning..."
                sudo rm -rf "$path" 2>/dev/null || rm -rf "$path" 2>/dev/null || true
                git clone --no-checkout "$url" "$path"
            }
        fi
    else
        echo "[$path] Cloning fresh..."
        sudo rm -rf "$path" 2>/dev/null || rm -rf "$path" 2>/dev/null || true
        # Clone with depth=1, then fetch specific ref if needed
        git clone --no-checkout --depth=1 "$url" "$path"
        # Fetch the specific commit if shallow clone doesn't have it
        if ! git -C "$path" cat-file -e "$ref" 2>/dev/null; then
            echo "[$path] Fetching specific ref $ref..."
            git -C "$path" fetch origin "$ref" --depth=1 2>/dev/null || \
            git -C "$path" fetch origin  # Full fetch as fallback
        fi
    fi

    echo "[$path] Checking out $ref..."
    if ! git -C "$path" checkout --force "$ref" 2>/dev/null; then
        echo "[$path] Checkout failed, fetching ref explicitly..."
        git -C "$path" fetch origin "$ref" --depth=1 2>/dev/null || git -C "$path" fetch origin
        git -C "$path" checkout --force "$ref"
    fi
    echo "[$path] Done"
}

echo "=== Fetching ${#PATHS[@]} submodule(s) ==="

if [[ "$PARALLEL" == true ]]; then
    echo "Running in parallel mode..."
    pids=()
    for path in "${PATHS[@]}"; do
        fetch_or_clone_submodule "$path" &
        pids+=($!)
    done

    # Wait for all background jobs and check for failures
    failed=0
    for pid in "${pids[@]}"; do
        if ! wait "$pid"; then
            failed=1
        fi
    done

    if [[ $failed -ne 0 ]]; then
        echo "ERROR: One or more submodule fetches failed"
        exit 1
    fi
else
    for path in "${PATHS[@]}"; do
        fetch_or_clone_submodule "$path"
    done
fi

echo "=== Submodules ready ==="
# Show status of fetched submodules
git submodule status "${PATHS[@]}" 2>/dev/null || true
