#!/bin/bash
# =============================================================================
# Extract HAF App Test Cache
# =============================================================================
# Shared script for HAF application CI test jobs to extract cached sync data.
# Consolidates common patterns from balance_tracker and haf_block_explorer.
#
# Features:
# - Exact cache key match only (no fallback to different app versions)
# - Marker file prevents redundant extractions in same pipeline
# - Optional PostgreSQL readiness wait
# - Handles pgdata permission fixing
# - Supports both HAF-style (haf_db_store/pgdata) and app-style (pgdata) structures
#
# Usage:
#   extract-test-cache.sh <cache-type> <cache-key> <dest-dir>
#
# Arguments:
#   cache-type  - Cache type (e.g., haf_btracker_sync, haf_hafbe_sync, haf_hivemind_sync, haf)
#   cache-key   - Cache key (e.g., ${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA})
#   dest-dir    - Destination directory for extracted cache
#
# Environment variables:
#   CACHE_MANAGER         - Path to cache-manager.sh (fetched if not set)
#   CACHE_MANAGER_REF     - Git ref for cache-manager (default: develop)
#   CI_PIPELINE_ID        - GitLab pipeline ID (for marker file)
#   POSTGRES_HOST         - PostgreSQL host for readiness check (default: none)
#   POSTGRES_PORT         - PostgreSQL port (default: 5432)
#   EXTRACT_TIMEOUT       - Timeout for PostgreSQL wait in seconds (default: 300)
#   SKIP_POSTGRES_WAIT    - Set to "true" to skip PostgreSQL wait
#   FORCE_EXTRACT         - Set to "1" to force extraction even if data exists (debug)
#
# Exit codes:
#   0 - Success
#   1 - Error (cache not found, extraction failed, or timeout)
# =============================================================================

set -euo pipefail

# Arguments
CACHE_TYPE="${1:?Usage: $0 <cache-type> <cache-key> <dest-dir>}"
CACHE_KEY="${2:?Usage: $0 <cache-type> <cache-key> <dest-dir>}"
DEST_DIR="${3:?Usage: $0 <cache-type> <cache-key> <dest-dir>}"

# Configuration from environment
CACHE_MANAGER="${CACHE_MANAGER:-/tmp/cache-manager.sh}"
CACHE_MANAGER_REF="${CACHE_MANAGER_REF:-develop}"
POSTGRES_HOST="${POSTGRES_HOST:-}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
EXTRACT_TIMEOUT="${EXTRACT_TIMEOUT:-300}"
SKIP_POSTGRES_WAIT="${SKIP_POSTGRES_WAIT:-false}"
FORCE_EXTRACT="${FORCE_EXTRACT:-0}"

# Marker file location
MARKER_FILE="${DEST_DIR}/.cache-ready"

echo "=== HAF App Test Cache Extraction ==="
echo "Cache type:  ${CACHE_TYPE}"
echo "Cache key:   ${CACHE_KEY}"
echo "Dest dir:    ${DEST_DIR}"
echo "Pipeline:    ${CI_PIPELINE_ID:-local}"
[[ "$FORCE_EXTRACT" == "1" ]] && echo "Force:       enabled (skipping cache checks)"
echo ""

# -----------------------------------------------------------------------------
# Fetch cache-manager if needed
# -----------------------------------------------------------------------------
if [[ ! -x "$CACHE_MANAGER" ]]; then
    echo "Fetching cache-manager from common-ci-configuration (ref: ${CACHE_MANAGER_REF})..."
    mkdir -p "$(dirname "$CACHE_MANAGER")"
    curl -fsSL "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/${CACHE_MANAGER_REF}/scripts/cache-manager.sh" -o "$CACHE_MANAGER"
    chmod +x "$CACHE_MANAGER"
fi

# -----------------------------------------------------------------------------
# Check if extraction already done for this pipeline
# -----------------------------------------------------------------------------
if [[ "$FORCE_EXTRACT" != "1" ]] && [[ -f "$MARKER_FILE" ]]; then
    MARKER_PIPELINE=$(cat "$MARKER_FILE" 2>/dev/null || echo "")
    if [[ "$MARKER_PIPELINE" == "${CI_PIPELINE_ID:-local}" ]]; then
        echo "Cache already extracted for this pipeline (marker: ${MARKER_PIPELINE})"
        echo "Skipping extraction"
        # Still wait for postgres if needed
        if [[ -n "$POSTGRES_HOST" ]] && [[ "$SKIP_POSTGRES_WAIT" != "true" ]]; then
            echo ""
            echo "=== Waiting for PostgreSQL ==="
            WAITED=0
            while ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q 2>/dev/null; do
                sleep 5
                WAITED=$((WAITED + 5))
                if [[ $WAITED -ge $EXTRACT_TIMEOUT ]]; then
                    echo "WARNING: PostgreSQL not ready after ${EXTRACT_TIMEOUT}s"
                    break
                fi
                echo "Waiting for PostgreSQL... (${WAITED}s)"
            done
            [[ $WAITED -lt $EXTRACT_TIMEOUT ]] && echo "PostgreSQL ready!"
        fi
        exit 0
    fi
    echo "Marker file exists but for different pipeline: ${MARKER_PIPELINE}"
fi

# -----------------------------------------------------------------------------
# Check if PostgreSQL is already running (files may be in use)
# -----------------------------------------------------------------------------
if [[ "$FORCE_EXTRACT" != "1" ]] && [[ -n "$POSTGRES_HOST" ]]; then
    if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q 2>/dev/null; then
        echo "PostgreSQL is already running - data may be in use"
        echo "Updating marker and skipping extraction"
        mkdir -p "${DEST_DIR}"
        echo "${CI_PIPELINE_ID:-local}" > "$MARKER_FILE"
        exit 0
    fi
fi

# -----------------------------------------------------------------------------
# Check if valid data already exists
# -----------------------------------------------------------------------------
# Support both HAF-style (haf_db_store/pgdata) and app-style (pgdata) structures
PGDATA=""
if [[ -d "${DEST_DIR}/datadir/haf_db_store/pgdata" ]] && [[ -f "${DEST_DIR}/datadir/haf_db_store/pgdata/PG_VERSION" ]]; then
    PGDATA="${DEST_DIR}/datadir/haf_db_store/pgdata"
elif [[ -d "${DEST_DIR}/datadir/pgdata" ]] && [[ -f "${DEST_DIR}/datadir/pgdata/PG_VERSION" ]]; then
    PGDATA="${DEST_DIR}/datadir/pgdata"
fi

if [[ "$FORCE_EXTRACT" != "1" ]] && [[ -n "$PGDATA" ]]; then
    echo "Valid PostgreSQL data exists at: $PGDATA"
    echo "Updating marker and skipping extraction"
    mkdir -p "${DEST_DIR}"
    echo "${CI_PIPELINE_ID:-local}" > "$MARKER_FILE"
    # Still wait for postgres if needed
    if [[ -n "$POSTGRES_HOST" ]] && [[ "$SKIP_POSTGRES_WAIT" != "true" ]]; then
        echo ""
        echo "=== Waiting for PostgreSQL ==="
        WAITED=0
        while ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q 2>/dev/null; do
            sleep 5
            WAITED=$((WAITED + 5))
            if [[ $WAITED -ge $EXTRACT_TIMEOUT ]]; then
                echo "WARNING: PostgreSQL not ready after ${EXTRACT_TIMEOUT}s"
                break
            fi
            echo "Waiting for PostgreSQL... (${WAITED}s)"
        done
        [[ $WAITED -lt $EXTRACT_TIMEOUT ]] && echo "PostgreSQL ready!"
    fi
    exit 0
fi

# -----------------------------------------------------------------------------
# Extract cache (exact key match only - no fallback)
# -----------------------------------------------------------------------------
echo "=== Extracting Cache ==="
echo "Key: ${CACHE_TYPE}/${CACHE_KEY}"

# Clean any existing partial data before extraction
# This prevents issues with different permissions or partial extractions
if [[ -d "${DEST_DIR}" ]]; then
    echo "Cleaning existing data at ${DEST_DIR}..."
    rm -rf "${DEST_DIR}" 2>/dev/null || sudo rm -rf "${DEST_DIR}" 2>/dev/null || true
fi
mkdir -p "${DEST_DIR}"

if CACHE_HANDLING=haf "$CACHE_MANAGER" get "${CACHE_TYPE}" "${CACHE_KEY}" "${DEST_DIR}"; then
    echo "Cache extracted successfully"
else
    echo ""
    echo "ERROR: Cache not found for key: ${CACHE_KEY}"
    echo ""
    echo "The sync job must complete successfully before test jobs can run."
    echo "Cache key includes both HAF commit and app commit to ensure schema compatibility."
    echo ""
    echo "Possible causes:"
    echo "  - Sync job did not complete successfully"
    echo "  - NFS cache not accessible from this runner"
    echo "  - Cache was cleaned up"
    exit 1
fi

# -----------------------------------------------------------------------------
# Fix PostgreSQL permissions (must be 700 for pg_ctl)
# -----------------------------------------------------------------------------
# Re-detect PGDATA after extraction (supports both HAF-style and app-style)
PGDATA=""
if [[ -d "${DEST_DIR}/datadir/haf_db_store/pgdata" ]]; then
    PGDATA="${DEST_DIR}/datadir/haf_db_store/pgdata"
elif [[ -d "${DEST_DIR}/datadir/pgdata" ]]; then
    PGDATA="${DEST_DIR}/datadir/pgdata"
fi

if [[ -n "$PGDATA" ]]; then
    echo ""
    echo "=== Fixing PostgreSQL Permissions ==="
    chmod 700 "$PGDATA" 2>/dev/null || sudo chmod 700 "$PGDATA" || true
    echo "Set $PGDATA permissions to 700"
fi

# -----------------------------------------------------------------------------
# Write marker file
# -----------------------------------------------------------------------------
echo "${CI_PIPELINE_ID:-local}" > "$MARKER_FILE"
echo "Created marker file: $MARKER_FILE"

# -----------------------------------------------------------------------------
# Wait for PostgreSQL if configured
# -----------------------------------------------------------------------------
if [[ -n "$POSTGRES_HOST" ]] && [[ "$SKIP_POSTGRES_WAIT" != "true" ]]; then
    echo ""
    echo "=== Waiting for PostgreSQL ==="
    echo "Host: ${POSTGRES_HOST}:${POSTGRES_PORT}"
    echo "Timeout: ${EXTRACT_TIMEOUT}s"

    WAITED=0
    while ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q 2>/dev/null; do
        sleep 5
        WAITED=$((WAITED + 5))
        if [[ $WAITED -ge $EXTRACT_TIMEOUT ]]; then
            echo ""
            echo "WARNING: PostgreSQL not ready after ${EXTRACT_TIMEOUT}s"
            echo "Container may not have started yet - this is OK if docker-compose runs after this script"
            exit 0
        fi
        echo "Waiting for PostgreSQL... (${WAITED}s)"
    done
    echo "PostgreSQL ready after ${WAITED}s"
fi

echo ""
echo "=== Done ==="
