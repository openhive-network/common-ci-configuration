#!/bin/bash
#
# Shared reconnect wrapper for HAF app block processing.
# Retries the given command on PostgreSQL connection errors (psql exit code 2).
# Exits immediately on success (exit 0) or SQL/application errors (exit 1, 3+).
#
# Usage:
#   run_with_reconnect.sh [OPTIONS] -- COMMAND [ARGS...]
#
# Options:
#   --max-retries=N   Maximum retry attempts (0 = unlimited, default: 0)
#   --retry-delay=N   Initial delay between retries in seconds (default: 5)
#   --max-delay=N     Maximum delay between retries in seconds (default: 60)
#
# Environment variables (override options):
#   MAX_RECONNECT_RETRIES  Same as --max-retries
#   RECONNECT_DELAY        Same as --retry-delay
#   RECONNECT_MAX_DELAY    Same as --max-delay
#
# psql exit codes:
#   0 = success
#   1 = internal psql error (out of memory, etc.)
#   2 = connection error (server down, connection lost) → RETRY
#   3 = SQL error (syntax, permission, schema issues)
#
# Can also be sourced to use run_with_reconnect() as a function.

set -o pipefail

_rwc_max_retries="${MAX_RECONNECT_RETRIES:-0}"
_rwc_retry_delay="${RECONNECT_DELAY:-5}"
_rwc_max_delay="${RECONNECT_MAX_DELAY:-60}"

_rwc_parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --max-retries=*)
                _rwc_max_retries="${1#*=}"
                ;;
            --retry-delay=*)
                _rwc_retry_delay="${1#*=}"
                ;;
            --max-delay=*)
                _rwc_max_delay="${1#*=}"
                ;;
            --)
                shift
                break
                ;;
            *)
                break
                ;;
        esac
        shift
    done
    _rwc_cmd=("$@")
}

run_with_reconnect() {
    local max_retries="${1:-$_rwc_max_retries}"
    local retry_delay="${2:-$_rwc_retry_delay}"
    local max_delay="${3:-$_rwc_max_delay}"
    shift 3 2>/dev/null || true

    local cmd=("${_rwc_cmd[@]}")
    if [ ${#cmd[@]} -eq 0 ]; then
        cmd=("$@")
    fi

    if [ ${#cmd[@]} -eq 0 ]; then
        echo "run_with_reconnect: no command specified" >&2
        return 1
    fi

    local attempt=0
    local delay="$retry_delay"

    while true; do
        attempt=$((attempt + 1))

        if [ "$attempt" -gt 1 ]; then
            echo "[$(date -uIseconds)] Reconnecting (attempt $attempt)..."
            # Reset healthcheck timer so container isn't marked unhealthy during retry
            if [ -f /tmp/block_processing_startup_time.txt ]; then
                date -uIseconds > /tmp/block_processing_startup_time.txt
            fi
        fi

        local exit_code=0
        "${cmd[@]}" || exit_code=$?

        if [ "$exit_code" -eq 0 ]; then
            return 0
        fi

        if [ "$exit_code" -ne 2 ]; then
            echo "[$(date -uIseconds)] Command failed with exit code $exit_code (not a connection error). Exiting." >&2
            return "$exit_code"
        fi

        if [ "$max_retries" -gt 0 ] && [ "$attempt" -ge "$max_retries" ]; then
            echo "[$(date -uIseconds)] Max retries ($max_retries) reached. Exiting." >&2
            return "$exit_code"
        fi

        # Add jitter: 0-25% of current delay
        local jitter=0
        if [ "$delay" -gt 4 ]; then
            jitter=$((RANDOM % (delay / 4 + 1)))
        fi
        local sleep_time=$((delay + jitter))

        echo "[$(date -uIseconds)] Connection lost (exit code 2). Waiting ${sleep_time}s before retry..." >&2
        sleep "$sleep_time"

        # Exponential backoff capped at max_delay
        delay=$((delay * 2))
        if [ "$delay" -gt "$max_delay" ]; then
            delay="$max_delay"
        fi
    done
}

# When executed directly (not sourced), parse args and run
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    _rwc_parse_args "$@"
    run_with_reconnect "$_rwc_max_retries" "$_rwc_retry_delay" "$_rwc_max_delay"
fi
