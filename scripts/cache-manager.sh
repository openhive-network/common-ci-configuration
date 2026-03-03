#!/bin/bash
#
# cache-manager.sh - Centralized CI cache manager with NFS backing
#
# Provides cross-builder cache sharing using NFS with LRU eviction.
# Designed for HAF replay caches and downstream project caches.
#
# Usage:
#   cache-manager.sh get <cache-type> <cache-key> <local-dest>
#   cache-manager.sh put <cache-type> <cache-key> <local-source>
#   cache-manager.sh cleanup <cache-type> [--max-size-gb N] [--max-age-days N]
#   cache-manager.sh cleanup-local [--max-size-gb N]   # Clean local cache only
#   cache-manager.sh cleanup-orphans [--max-age-days N] [--dry-run]  # Clean stale dirs (orphans + old extractions)
#   cache-manager.sh list <cache-type>
#   cache-manager.sh status
#   cache-manager.sh is-fast-builder    # Check if current host is a fast builder
#
# CI Tag Requirements:
#   Replay/build jobs should use: tags: [data-cache-storage, fast]
#   Fast builders (AMD 5950): hive-builder-8, hive-builder-9, hive-builder-10
#
# Cache types: hive, haf, balance_tracker, hivemind, etc.
#
# Environment variables:
#   CACHE_NFS_PATH        - NFS mount point (default: /nfs/ci-cache)
#   CACHE_LOCAL_PATH      - Local cache directory (default: /cache)
#   CACHE_MAX_SIZE_GB     - Max total NFS cache size (default: 4000)
#   CACHE_LOCAL_MAX_GB    - Max local cache size (default: 3000)
#   CACHE_MAX_AGE_DAYS    - Max cache age (default: 30)
#   CACHE_LOCK_TIMEOUT    - Lock timeout in seconds (default: 3600)
#   CACHE_QUIET           - Suppress verbose output (default: false)
#   SHARED_BLOCK_LOG_LOCAL - Local block_log path (default: /blockchain/block_log_5m)
#   SHARED_BLOCK_LOG_NFS   - NFS block_log path (default: /nfs/ci-cache/hive/blockchain/block_log_5m)

set -euo pipefail

# Check for proper flock support (util-linux, not BusyBox)
# BusyBox flock returns "Bad file descriptor" on NFS mounts and lacks -w timeout support
_check_flock_support() {
    # Check if flock supports -w (timeout) - util-linux does, BusyBox doesn't
    if ! flock --help 2>&1 | grep -q -- '-w'; then
        _error "BusyBox flock detected - this does not work with NFS!"
        _error "Install util-linux package: apk add util-linux (Alpine) or apt install util-linux (Debian)"
        _error "Docker images docker-builder and docker-dind should already have util-linux installed."
        exit 1
    fi
}

# Wrapper for flock with timeout
# Usage: _flock_with_timeout <timeout> <mode> <lockfile> <command...>
#   mode: -s (shared) or -x (exclusive)
_flock_with_timeout() {
    local timeout="$1"
    local mode="$2"
    local lockfile="$3"
    shift 3

    flock "$mode" -w "$timeout" "$lockfile" "$@"
}

# Configuration with defaults
CACHE_NFS_PATH="${CACHE_NFS_PATH:-/nfs/ci-cache}"
CACHE_LOCAL_PATH="${CACHE_LOCAL_PATH:-/cache}"
CACHE_MAX_SIZE_GB="${CACHE_MAX_SIZE_GB:-4000}"
CACHE_LOCAL_MAX_GB="${CACHE_LOCAL_MAX_GB:-3000}"
CACHE_MAX_AGE_DAYS="${CACHE_MAX_AGE_DAYS:-30}"
CACHE_LOCK_TIMEOUT="${CACHE_LOCK_TIMEOUT:-120}"  # 2 minutes (NFS writes take ~10s, 12x margin)
CACHE_STALE_LOCK_MINUTES="${CACHE_STALE_LOCK_MINUTES:-10}"  # Break locks older than this (writes take ~10s)
CACHE_QUIET="${CACHE_QUIET:-false}"

# Shared block_log locations (used when blockchain excluded from cache)
SHARED_BLOCK_LOG_LOCAL="${SHARED_BLOCK_LOG_LOCAL:-/blockchain/block_log_5m}"
SHARED_BLOCK_LOG_NFS="${SHARED_BLOCK_LOG_NFS:-/nfs/ci-cache/hive/blockchain/block_log_5m}"

# Completion marker - written after successful extraction to indicate cache is valid
# If this file is missing, the extraction was interrupted and the cache is incomplete
CACHE_COMPLETION_MARKER=".extraction_complete"

# Logging
_log() {
    if [[ "$CACHE_QUIET" != "true" ]]; then
        echo "[cache-manager] $1" >&2
    fi
}

_error() {
    echo "[cache-manager] ERROR: $1" >&2
}

# Check if an extraction is complete (has completion marker)
# Returns 0 if complete, 1 if incomplete/missing
_is_extraction_complete() {
    local dest_dir="$1"
    [[ -f "${dest_dir}/${CACHE_COMPLETION_MARKER}" ]]
}

# Write completion marker after successful extraction
_write_completion_marker() {
    local dest_dir="$1"
    local marker_file="${dest_dir}/${CACHE_COMPLETION_MARKER}"

    # Write marker with metadata for debugging
    cat > "$marker_file" 2>/dev/null <<EOF || true
timestamp=$(date -Iseconds)
hostname=$(hostname)
job_id=${CI_JOB_ID:-unknown}
pipeline_id=${CI_PIPELINE_ID:-unknown}
EOF
    chmod 644 "$marker_file" 2>/dev/null || true
}

# Validate that an extracted cache is complete and usable
# Checks both completion marker and basic structure
_validate_extraction() {
    local dest_dir="$1"
    local cache_type="$2"

    # Must have datadir
    if [[ ! -d "${dest_dir}/datadir" ]]; then
        return 1
    fi

    # Must have completion marker
    if ! _is_extraction_complete "$dest_dir"; then
        _log "Extraction incomplete (missing ${CACHE_COMPLETION_MARKER})"
        return 1
    fi

    # For HAF caches, validate pgdata structure
    if [[ "$cache_type" == haf* ]]; then
        local pgdata="${dest_dir}/datadir/haf_db_store/pgdata"
        if [[ ! -d "$pgdata" ]]; then
            _log "HAF cache validation failed: pgdata missing"
            return 1
        fi
        # Check for PG_VERSION as basic sanity check
        if ! sudo test -f "${pgdata}/PG_VERSION" 2>/dev/null; then
            _log "HAF cache validation failed: PG_VERSION missing"
            return 1
        fi
    fi

    return 0
}

# Create or update a lock file with world-writable permissions
# This ensures lock files can be used by any user/container (different UIDs)
_touch_lock() {
    local lockfile="$1"
    if [[ ! -f "$lockfile" ]]; then
        # Create new lock file with 666 permissions
        install -m 666 /dev/null "$lockfile" 2>/dev/null || touch "$lockfile" 2>/dev/null || true
    else
        # Update timestamp, fix permissions if we can
        touch "$lockfile" 2>/dev/null || true
        chmod 666 "$lockfile" 2>/dev/null || true
    fi
}

# Create cache type directory with world-writable permissions
# This ensures different container UIDs can write to the shared NFS cache
_ensure_cache_dir() {
    local dir="$1"
    mkdir -p "$dir" 2>/dev/null || sudo mkdir -p "$dir" 2>/dev/null || true
    # Make directory world-writable so any UID can create files
    # Try without sudo first, then with sudo if that fails
    chmod 777 "$dir" 2>/dev/null || sudo chmod 777 "$dir" 2>/dev/null || true
}

# Write lock holder info for debugging stale locks
_write_lock_info() {
    local lockfile="$1"
    local infofile="${lockfile}.info"
    cat > "$infofile" 2>/dev/null <<EOF || true
hostname=$(hostname)
pid=$$
started=$(date -Iseconds)
job_id=${CI_JOB_ID:-unknown}
pipeline_id=${CI_PIPELINE_ID:-unknown}
EOF
}

# Check for and clean stale locks
# Returns 0 if lock was stale and cleaned, 1 otherwise
_check_stale_lock() {
    local lockfile="$1"
    local stale_minutes="${CACHE_STALE_LOCK_MINUTES:-10}"

    # If lock file doesn't exist, nothing to check
    [[ -f "$lockfile" ]] || return 1

    # Check if lock file is older than stale threshold
    local lock_age_minutes
    lock_age_minutes=$(( ($(date +%s) - $(stat -c %Y "$lockfile" 2>/dev/null || echo 0)) / 60 ))

    if [[ $lock_age_minutes -lt $stale_minutes ]]; then
        return 1  # Not stale yet
    fi

    # Lock file is old - check if anyone is actually holding it
    if flock -n "$lockfile" -c "true" 2>/dev/null; then
        # Lock is not held, just stale file - clean it up silently
        rm -f "$lockfile" "${lockfile}.info" 2>/dev/null || true
        return 0  # Cleaned stale file
    fi

    # Lock IS held but file is very old - likely stale NFS lock
    _log "WARNING: Lock file is ${lock_age_minutes} minutes old and appears stuck"

    # Read lock holder info if available
    local infofile="${lockfile}.info"
    if [[ -f "$infofile" ]]; then
        _log "Lock holder info:"
        cat "$infofile" >&2 || true
    fi

    # Break the stale lock
    _log "Breaking stale lock (${lock_age_minutes} min old, threshold: ${stale_minutes} min)"
    rm -f "$lockfile" "${lockfile}.info" 2>/dev/null || true
    return 0  # Lock was broken
}

# Clean up all stale lock files in a directory
_cleanup_stale_locks() {
    local dir="$1"
    local stale_minutes="${CACHE_STALE_LOCK_MINUTES:-10}"
    local cleaned=0

    for lockfile in "$dir"/*.lock "$dir"/*/*.lock; do
        [[ -f "$lockfile" ]] || continue
        if _check_stale_lock "$lockfile"; then
            cleaned=$((cleaned + 1))
        fi
    done

    if [[ $cleaned -gt 0 ]]; then _log "Cleaned up $cleaned stale lock files"; fi
}

# Check if running on the NFS host (where NFS path is local, not a mount)
# On NFS host: /nfs/ci-cache is a symlink to /storage1/ci-cache (local storage)
# On clients: /nfs/ci-cache is an NFS mount point
_is_nfs_host() {
    # If it's a symlink, we're on the NFS host
    if [[ -L "$CACHE_NFS_PATH" ]]; then
        return 0
    fi
    # If it exists but is NOT a mount point, we're on the NFS host
    if [[ -d "$CACHE_NFS_PATH" ]] && ! mountpoint -q "$CACHE_NFS_PATH" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Check if NFS is mounted and accessible (or we're on the NFS host)
_nfs_available() {
    # On NFS host, the path is local (symlink or direct), not a mount
    if _is_nfs_host; then
        [[ -d "$CACHE_NFS_PATH" ]]
        return $?
    fi
    # On clients, check for mount
    [[ -d "$CACHE_NFS_PATH" ]] && mountpoint -q "$CACHE_NFS_PATH" 2>/dev/null
}

# Get paths for a cache entry
_get_paths() {
    local cache_type="$1"
    local cache_key="$2"

    NFS_CACHE_DIR="${CACHE_NFS_PATH}/${cache_type}/${cache_key}"
    NFS_TAR_FILE="${NFS_CACHE_DIR}.tar"
    NFS_TAR_LOCK="${NFS_TAR_FILE}.lock"

    # Local cache is always a tar file (immutable, extracted fresh each time)
    # On NFS host, local tar IS the NFS tar (same filesystem)
    if _is_nfs_host; then
        LOCAL_TAR_FILE="$NFS_TAR_FILE"
    else
        LOCAL_TAR_FILE="${CACHE_LOCAL_PATH}/${cache_type}_${cache_key}.tar"
    fi

    METADATA_FILE="${NFS_CACHE_DIR}/.metadata"
    LRU_INDEX="${CACHE_NFS_PATH}/.lru_index"
    GLOBAL_LOCK="${CACHE_NFS_PATH}/.global_lock"
}

# Update LRU index with access timestamp
_update_lru() {
    local cache_type="$1"
    local cache_key="$2"
    local timestamp=$(date +%s)
    local entry="${cache_type}/${cache_key}"

    # Acquire global lock for index update
    _touch_lock "$GLOBAL_LOCK"
    _flock_with_timeout 30 -x "$GLOBAL_LOCK" -c "
        # Create or update LRU index (simple format: timestamp|path per line)
        if [ -f '$LRU_INDEX' ]; then
            # Remove old entry and add new one
            grep -v '^[0-9]*|${entry}\$' '$LRU_INDEX' > '${LRU_INDEX}.tmp' 2>/dev/null || true
            echo '${timestamp}|${entry}' >> '${LRU_INDEX}.tmp'
            mv '${LRU_INDEX}.tmp' '$LRU_INDEX'
        else
            echo '${timestamp}|${entry}' > '$LRU_INDEX'
        fi
    " || _error "Failed to acquire global lock for LRU update"
}

# Write metadata for a cache entry
_write_metadata() {
    local cache_type="$1"
    local cache_key="$2"
    local source_dir="$3"

    local timestamp=$(date -Iseconds)
    local size=$(du -sb "$source_dir" 2>/dev/null | cut -f1 || echo 0)
    local hostname=$(hostname)

    cat > "$METADATA_FILE" <<EOF
{
    "cache_type": "${cache_type}",
    "cache_key": "${cache_key}",
    "created_at": "${timestamp}",
    "size_bytes": ${size},
    "source_builder": "${hostname}",
    "ci_pipeline_id": "${CI_PIPELINE_ID:-unknown}",
    "ci_job_id": "${CI_JOB_ID:-unknown}"
}
EOF
}

# Fix pg_tblspc symlinks to use relative paths
# PostgreSQL creates symlinks like pg_tblspc/16396 -> /home/hived/datadir/haf_db_store/tablespace
# These absolute paths become invalid when data is extracted to a different location or mounted inside containers
# We update them to use relative paths (../../tablespace) which work in any location
_fix_pg_tblspc_symlinks() {
    local source_dir="$1"
    local pg_tblspc="${source_dir}/datadir/haf_db_store/pgdata/pg_tblspc"
    local tablespace_dir="${source_dir}/datadir/haf_db_store/tablespace"

    if [[ ! -d "$pg_tblspc" ]]; then
        return 0
    fi

    # Relative path from pg_tblspc/16396 to tablespace is ../../tablespace
    # This works both on the host AND inside Docker containers where datadir is mounted at a different path
    local relative_path="../../tablespace"

    # Find all symlinks in pg_tblspc and update to point to current tablespace location
    for link in "$pg_tblspc"/*; do
        if [[ -L "$link" ]]; then
            local link_name
            link_name=$(basename "$link")
            local target
            target=$(readlink "$link")

            # Check if target contains 'tablespace' (the directory we need to point to)
            if [[ "$target" == *"tablespace"* ]] && [[ -d "$tablespace_dir" ]]; then
                _log "Fixing pg_tblspc symlink: $link_name (was -> $target)"
                # Remove old symlink and create new one with relative path
                # Use sudo since symlink may be owned by postgres (uid 105)
                sudo rm -f "$link" 2>/dev/null || rm -f "$link"
                sudo ln -s "$relative_path" "$link" 2>/dev/null || ln -s "$relative_path" "$link"
                _log "Fixed pg_tblspc symlink: $link_name -> $relative_path"
            fi
        fi
    done
}

# Convert pg_tblspc absolute symlinks to relative symlinks
# This ensures symlinks work correctly when data is copied to different locations
_convert_pg_tblspc_to_relative() {
    local source_dir="$1"
    local pg_tblspc="${source_dir}/datadir/haf_db_store/pgdata/pg_tblspc"

    if [[ ! -d "$pg_tblspc" ]]; then
        return 0
    fi

    # Relative path from pg_tblspc to tablespace is ../../tablespace
    local relative_path="../../tablespace"

    for link in "$pg_tblspc"/*; do
        if [[ -L "$link" ]]; then
            local link_name
            link_name=$(basename "$link")
            local target
            target=$(readlink "$link")

            # Only convert if it's an absolute path pointing to tablespace
            if [[ "$target" == /* ]] && [[ "$target" == *"tablespace"* ]]; then
                _log "Converting pg_tblspc symlink to relative: $link_name"
                sudo rm -f "$link" 2>/dev/null || rm -f "$link"
                sudo ln -s "$relative_path" "$link" 2>/dev/null || ln -s "$relative_path" "$link"
            fi
        fi
    done
}

# Relax PostgreSQL pgdata permissions for caching
# Makes pgdata and tablespace readable so they can be copied to NFS
_relax_pgdata_permissions() {
    local source_dir="$1"
    local haf_db_store="${source_dir}/datadir/haf_db_store"
    local pgdata_path="${haf_db_store}/pgdata"
    local tablespace_path="${haf_db_store}/tablespace"

    if [[ -d "$pgdata_path" ]]; then
        _log "Relaxing pgdata permissions for caching"
        # Make readable for copying (PostgreSQL creates mode 700)
        sudo chmod -R a+rX "$pgdata_path" 2>/dev/null || chmod -R a+rX "$pgdata_path" 2>/dev/null || true
    fi

    if [[ -d "$tablespace_path" ]]; then
        _log "Relaxing tablespace permissions for caching"
        sudo chmod -R a+rX "$tablespace_path" 2>/dev/null || chmod -R a+rX "$tablespace_path" 2>/dev/null || true
    fi

    # Convert absolute symlinks to relative so they work when copied anywhere
    _convert_pg_tblspc_to_relative "$source_dir"
}

# Restore PostgreSQL pgdata permissions after cache retrieval
# pgdata must be mode 700 or 750, owned by postgres user for PostgreSQL to start
_restore_pgdata_permissions() {
    local dest_dir="$1"
    local haf_db_store="${dest_dir}/datadir/haf_db_store"
    local pgdata_path="${haf_db_store}/pgdata"
    local tablespace_path="${haf_db_store}/tablespace"

    # Fix tablespace symlinks in case cache was created before symlink fixing was enabled
    _fix_pg_tblspc_symlinks "$dest_dir"

    if [[ -d "$pgdata_path" ]]; then
        _log "Restoring pgdata permissions to mode 700"
        # Restore strict permissions required by PostgreSQL
        sudo chmod 700 "$pgdata_path" 2>/dev/null || chmod 700 "$pgdata_path" 2>/dev/null || true
        # Restore ownership to postgres user (uid 105 in HAF containers)
        sudo chown -R 105:105 "$pgdata_path" 2>/dev/null || true
    fi

    if [[ -d "$tablespace_path" ]]; then
        _log "Restoring tablespace permissions"
        sudo chmod 700 "$tablespace_path" 2>/dev/null || chmod 700 "$tablespace_path" 2>/dev/null || true
        sudo chown -R 105:105 "$tablespace_path" 2>/dev/null || true
    fi
}

# Validate PostgreSQL pgdata directory integrity before caching
# PostgreSQL requires certain directories to exist, even if empty.
# If these are missing, the database cannot start and the cache is corrupted.
# Returns 0 if valid, 1 if invalid (with error messages)
_validate_pgdata_integrity() {
    local source_dir="$1"
    local pgdata_path="${source_dir}/datadir/haf_db_store/pgdata"

    if [[ ! -d "$pgdata_path" ]]; then
        _log "No pgdata directory found at $pgdata_path - skipping validation"
        return 0
    fi

    # Required PostgreSQL directories (must exist, can be empty)
    # These are created by initdb and required for PostgreSQL to start
    local required_dirs=(
        "pg_notify"      # LISTEN/NOTIFY async notifications
        "pg_serial"      # Serializable transaction info
        "pg_snapshots"   # Exported snapshots
        "pg_replslot"    # Replication slots
        "pg_dynshmem"    # Dynamic shared memory
        "pg_commit_ts"   # Commit timestamps
        "pg_stat"        # Statistics subsystem
        "pg_logical"     # Logical replication
        "pg_subtrans"    # Subtransaction status
        "pg_multixact"   # Multixact status
        "pg_twophase"    # Two-phase commit state
        "pg_tblspc"      # Tablespace symlinks
        "pg_wal"         # Write-ahead log
    )

    # Note: pgdata is owned by postgres, so we need sudo to check directories
    # The -d test fails without execute permission on the parent directory
    local missing_dirs=()
    for dir in "${required_dirs[@]}"; do
        if ! sudo test -d "${pgdata_path}/${dir}" 2>/dev/null; then
            missing_dirs+=("$dir")
        fi
    done

    if [[ ${#missing_dirs[@]} -gt 0 ]]; then
        _error "PostgreSQL pgdata is CORRUPTED - missing required directories:"
        for dir in "${missing_dirs[@]}"; do
            _error "  - ${dir}/"
        done
        _error ""
        _error "This usually indicates PostgreSQL was not shut down cleanly."
        _error "The cache will NOT be saved to prevent propagating corruption."
        _error ""
        _error "Possible causes:"
        _error "  1. PostgreSQL checkpoint failed before shutdown"
        _error "  2. Container was force-killed (SIGKILL) without graceful stop"
        _error "  3. pg_ctl stop was not used for proper shutdown"
        _error ""
        _error "Fix: Ensure the sync job uses pg_ctl stop before docker-compose down"
        return 1
    fi

    # Also check for critical files (using sudo for same permission reasons)
    local required_files=(
        "PG_VERSION"
        "postgresql.auto.conf"
    )

    local missing_files=()
    for file in "${required_files[@]}"; do
        if ! sudo test -f "${pgdata_path}/${file}" 2>/dev/null; then
            missing_files+=("$file")
        fi
    done

    if [[ ${#missing_files[@]} -gt 0 ]]; then
        _error "PostgreSQL pgdata is CORRUPTED - missing required files:"
        for file in "${missing_files[@]}"; do
            _error "  - ${file}"
        done
        return 1
    fi

    _log "pgdata integrity check passed - all required directories present"
    return 0
}

# Build tar exclusion arguments for HAF caches to reduce size
# Excludes: blockchain (use shared block_log via _link_shared_block_log)
# NOTE: We keep ALL WAL files to ensure safe PostgreSQL recovery.
# Previously we tried to exclude WAL files except the checkpoint WAL to save ~5.8GB,
# but this caused data corruption when PostgreSQL started crash recovery on extracted data.
# Build tar exclusions for block_log files only (not entire blockchain directory)
# This preserves RocksDB directories (account-history-rocksdb-storage, comments-rocksdb-storage)
# which contain unique replay data, while excluding block_log files that are shared.
_build_blockchain_tar_excludes() {
    local source_dir="$1"
    local blockchain_dir="${source_dir}/datadir/blockchain"
    local excludes=""

    if [[ ! -d "$blockchain_dir" ]]; then
        echo ""
        return 0
    fi

    # Exclude only block_log files - these are large (~1.7GB) and shared via symlinks
    # Keep RocksDB directories which contain unique replay state data
    excludes="--exclude=./datadir/blockchain/block_log"
    excludes+=" --exclude=./datadir/blockchain/block_log.artifacts"

    # Exclude all block_log_part.* files and their artifacts
    for part_file in "${blockchain_dir}"/block_log_part.*; do
        if [[ -e "$part_file" ]]; then
            local filename
            filename=$(basename "$part_file")
            excludes+=" --exclude=./datadir/blockchain/${filename}"
        fi
    done

    _log "Excluding block_log files (will link shared block_log on extraction)"
    _log "Preserving RocksDB directories in cache"
    echo "$excludes"
}

# The tar may be created while PostgreSQL is still running (docker-compose down takes time),
# so we need all WAL files for proper recovery.
_build_haf_tar_excludes() {
    local source_dir="$1"

    # Use common block_log exclusion logic
    local excludes
    excludes=$(_build_blockchain_tar_excludes "$source_dir")

    # Keep all pg_wal files - required for safe PostgreSQL recovery

    echo "$excludes"
}

# Link shared block_log into extracted HAF cache
# Called after extraction when blockchain was excluded from cache
# Checks local path first (each builder has block_log), then NFS as fallback
_link_shared_block_log() {
    local dest_dir="$1"
    local blockchain_dir="${dest_dir}/datadir/blockchain"

    # If block_log files already exist in extracted data, nothing to do
    # Note: Other files like haf_wal may exist - we only care about block_log*
    if ls "${blockchain_dir}"/block_log* 1>/dev/null 2>&1; then
        _log "block_log files exist in cache, skipping block_log linking"
        return 0
    fi

    # Shared block_log locations - check local first (faster), then NFS
    # Paths are configurable via SHARED_BLOCK_LOG_LOCAL and SHARED_BLOCK_LOG_NFS env vars
    local shared_block_log=""

    if [[ -d "$SHARED_BLOCK_LOG_LOCAL" ]] && [[ -n "$(ls -A "$SHARED_BLOCK_LOG_LOCAL" 2>/dev/null)" ]]; then
        shared_block_log="$SHARED_BLOCK_LOG_LOCAL"
        _log "Using local shared block_log: $SHARED_BLOCK_LOG_LOCAL"
    elif [[ -d "$SHARED_BLOCK_LOG_NFS" ]] && [[ -n "$(ls -A "$SHARED_BLOCK_LOG_NFS" 2>/dev/null)" ]]; then
        shared_block_log="$SHARED_BLOCK_LOG_NFS"
        _log "Using NFS shared block_log: $SHARED_BLOCK_LOG_NFS"
    else
        _log "WARNING: No shared block_log found at $SHARED_BLOCK_LOG_LOCAL or $SHARED_BLOCK_LOG_NFS"
        return 0
    fi

    # Create blockchain directory and symlinks
    _log "Linking shared block_log into ${blockchain_dir}"
    mkdir -p "$blockchain_dir"

    for block_file in "${shared_block_log}"/block_log*; do
        if [[ -f "$block_file" ]]; then
            local filename
            filename=$(basename "$block_file")
            ln -sf "$block_file" "${blockchain_dir}/${filename}"
            _log "Linked: ${filename}"
        fi
    done

    ls -la "$blockchain_dir" 2>/dev/null || true
}

# Clean up stale extraction directory if it exists with permission issues
# After extraction, _restore_pgdata_permissions changes ownership to postgres (UID 105)
# with mode 700. On the next run, tar can't overwrite because the runner runs as a
# different user (e.g., UID 2000). This function detects and removes such stale directories.
_cleanup_stale_extraction() {
    local dest_dir="$1"

    # If directory doesn't exist, nothing to clean
    [[ -d "$dest_dir" ]] || return 0

    # Test if we can write to the directory
    if touch "${dest_dir}/.write_test" 2>/dev/null; then
        rm -f "${dest_dir}/.write_test"
        return 0  # Directory is writable, no cleanup needed
    fi

    # Directory exists but we can't write to it - stale extraction with wrong permissions
    _log "Stale extraction detected with permission issues, cleaning up: $dest_dir"

    # Try to remove with sudo first (for files owned by postgres UID 105), fall back to regular rm
    if sudo rm -rf "$dest_dir" 2>/dev/null; then
        _log "Cleaned up stale extraction (with sudo)"
    elif rm -rf "$dest_dir" 2>/dev/null; then
        _log "Cleaned up stale extraction"
    else
        _error "Failed to clean up stale extraction directory: $dest_dir"
        return 1
    fi

    return 0
}

# GET: Check local tar, then NFS tar, extract to destination
# Local cache is immutable (tar file) - extracted fresh each time for safety
cmd_get() {
    local cache_type="$1"
    local cache_key="$2"
    local local_dest="$3"

    _get_paths "$cache_type" "$cache_key"

    local is_nfs_host=false
    _is_nfs_host && is_nfs_host=true

    # 1. Ensure we have a local tar file (copy from NFS if needed)
    # Always extract from local for faster I/O
    local use_local_cache=false
    if [[ -f "$LOCAL_TAR_FILE" ]]; then
        # Local tar exists - but validate it's complete before using
        local tar_lock="${LOCAL_TAR_FILE}.lock"
        use_local_cache=true

        # Check 1: Is a concurrent PUT in progress? (exclusive lock held on .lock file)
        # If the lock file exists and we can't acquire a shared lock instantly, PUT is writing
        if [[ -f "$tar_lock" ]] && ! _flock_with_timeout 0 -s "$tar_lock" -c "exit 0" 2>/dev/null; then
            _log "Local tar exists but PUT is in progress (lock held) - will fetch from NFS"
            use_local_cache=false
        # Check 2: If NFS tar exists, verify local size matches (detect truncated/stale files)
        elif [[ "$is_nfs_host" != "true" ]] && _nfs_available && [[ -f "$NFS_TAR_FILE" ]]; then
            local local_size nfs_size
            local_size=$(stat -c %s "$LOCAL_TAR_FILE" 2>/dev/null || echo 0)
            nfs_size=$(stat -c %s "$NFS_TAR_FILE" 2>/dev/null || echo 0)
            if [[ "$local_size" -lt "$nfs_size" ]]; then
                _log "Local tar (${local_size} bytes) smaller than NFS (${nfs_size} bytes) - stale/incomplete"
                _log "Removing stale local tar and fetching from NFS"
                rm -f "$LOCAL_TAR_FILE"
                use_local_cache=false
            fi
        fi

        if [[ "$use_local_cache" == "true" ]]; then
            _log "Local cache hit: $LOCAL_TAR_FILE"
        fi
    fi

    if [[ "$use_local_cache" != "true" ]]; then
        # No valid local cache - try NFS
        if [[ "$is_nfs_host" == "true" ]]; then
            # On NFS host, local and NFS are the same - if local miss, it's a miss
            _log "NFS host cache miss: $NFS_TAR_FILE"
            return 1
        elif ! _nfs_available; then
            _log "NFS not available, cache miss"
            return 1
        fi

        # NFS negative dentry caching can cause a file to appear missing for
        # seconds after it was created on another builder. On Linux 6.17+ with
        # NFSv4.2, negative dentries for UIDs unknown to the NFS server are
        # cached indefinitely (ignoring acdirmax), so stat()-based checks like
        # [[ -f file ]] never see the file. Work around this by using open()
        # (head -c 1) which bypasses the negative dentry cache.
        if ! [[ -f "$NFS_TAR_FILE" ]]; then
            for _nfs_retry in 1 2 3 4 5; do
                sleep 5
                # Use head (open syscall) instead of test -f (stat syscall)
                # to bypass NFS negative dentry cache bug on kernel 6.17+
                if head -c 1 "$NFS_TAR_FILE" >/dev/null 2>&1 || [[ -f "$NFS_TAR_FILE" ]]; then
                    _log "NFS file appeared after ${_nfs_retry} retries (NFS cache staleness)"
                    break
                fi
            done
        fi

        if head -c 1 "$NFS_TAR_FILE" >/dev/null 2>&1 || [[ -f "$NFS_TAR_FILE" ]]; then
            # Copy NFS tar to local FIRST, then extract from local (faster)
            # Use locking + atomic rename to prevent concurrent jobs from reading incomplete files
            _log "NFS cache hit: $NFS_TAR_FILE - copying to local cache"
            mkdir -p "$(dirname "$LOCAL_TAR_FILE")"

            local local_copy_lock="${LOCAL_TAR_FILE}.copylock"
            _touch_lock "$local_copy_lock"

            # Try to acquire exclusive lock (wait up to 60s for another job to finish copying)
            if _flock_with_timeout 60 -x "$local_copy_lock" -c "
                # Re-check if file appeared while waiting (another job finished copying)
                if [ -f '$LOCAL_TAR_FILE' ]; then
                    echo '[cache-manager] Local cache appeared while waiting for lock' >&2
                    exit 0
                fi

                copy_start=\$(date +%s.%N)
                # Use atomic rename: copy to .tmp first, then mv to final name
                if cp '$NFS_TAR_FILE' '${LOCAL_TAR_FILE}.tmp' && mv '${LOCAL_TAR_FILE}.tmp' '$LOCAL_TAR_FILE'; then
                    copy_end=\$(date +%s.%N)
                    copy_duration=\$(echo \"\$copy_end - \$copy_start\" | bc 2>/dev/null || echo '?')
                    tar_size=\$(stat -c %s '$LOCAL_TAR_FILE' 2>/dev/null || echo 0)
                    throughput=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / \$copy_duration\" | bc 2>/dev/null || echo '?')
                    echo \"[cache-manager] Copied to local cache in \${copy_duration}s (\${throughput} MB/s)\" >&2
                else
                    echo '[cache-manager] ERROR: Failed to copy NFS tar to local cache' >&2
                    rm -f '${LOCAL_TAR_FILE}.tmp'
                    exit 1
                fi
            "; then
                : # Success - file is now in local cache (either we copied it or another job did)
            else
                _error "Lock timeout or copy failed, falling back to direct NFS extraction"
                rm -f "${LOCAL_TAR_FILE}.tmp"
                # Fall back to extracting directly from NFS tar
                LOCAL_TAR_FILE="$NFS_TAR_FILE"
            fi
        else
            _log "Cache miss: $NFS_TAR_FILE"
            return 1
        fi
    fi

    # 2. Extract from LOCAL tar with exclusive locking on destination
    # Use exclusive lock on destination directory to prevent race conditions where multiple
    # jobs on the same builder try to extract to the same location simultaneously.
    # This was causing "Cannot open: File exists" errors when concurrent extractions collided.

    local dest_lock="${local_dest}.lock"
    _touch_lock "$dest_lock"

    # Check for stale locks before attempting to acquire
    # Note: _check_stale_lock returns 1 if not stale, which would trigger errexit
    _check_stale_lock "$dest_lock" || true

    local get_start_time=$(date +%s.%N)
    if _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$dest_lock" -c "
        lock_acquired=\$(date +%s.%N)
        echo \"[cache-manager] Exclusive lock acquired in \$(echo \"\$lock_acquired - $get_start_time\" | bc 2>/dev/null || echo '?')s\" >&2

        # Re-check inside lock: another job may have finished extraction while we waited
        # Must have BOTH datadir AND completion marker to be considered valid
        if [ -d '${local_dest}/datadir' ] && [ -f '${local_dest}/${CACHE_COMPLETION_MARKER}' ]; then
            echo '[cache-manager] Cache already extracted by another job (verified complete), skipping extraction' >&2
            exit 0
        fi

        # Clean up incomplete/stale extraction if present
        # Cases: permission issues, missing completion marker, or interrupted extraction
        if [ -d '${local_dest}' ]; then
            needs_cleanup=false

            # Check for permission issues (can't write)
            if ! touch '${local_dest}/.write_test' 2>/dev/null; then
                echo '[cache-manager] Stale extraction with permission issues, cleaning up' >&2
                needs_cleanup=true
            else
                rm -f '${local_dest}/.write_test'
            fi

            # Check for incomplete extraction (datadir exists but no completion marker)
            if [ -d '${local_dest}/datadir' ] && [ ! -f '${local_dest}/${CACHE_COMPLETION_MARKER}' ]; then
                echo '[cache-manager] Incomplete extraction detected (missing completion marker), cleaning up' >&2
                needs_cleanup=true
            fi

            if [ \"\$needs_cleanup\" = true ]; then
                sudo rm -rf '${local_dest}' 2>/dev/null || rm -rf '${local_dest}' 2>/dev/null || true
                if [ -d '${local_dest}' ]; then
                    echo '[cache-manager] ERROR: Failed to clean up stale extraction at ${local_dest}' >&2
                    echo '[cache-manager] This usually means files are owned by a different user (uid 105)' >&2
                    exit 1
                fi
            fi
        fi
        mkdir -p '${local_dest}'

        tar_size=\$(stat -c %s '$LOCAL_TAR_FILE' 2>/dev/null || echo 0)
        tar_size_gb=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / 1024\" | bc 2>/dev/null || echo '?')
        echo \"[cache-manager] Extracting (\${tar_size_gb}GB) to: $local_dest\" >&2

        extract_start=\$(date +%s.%N)
        if ! tar xf '$LOCAL_TAR_FILE' -C '$local_dest'; then
            # Tar failed (e.g. symlink already exists from a previous valid extraction)
            # If a usable cache is already present, use it instead of failing
            if [ -d '${local_dest}/datadir' ] && [ -f '${local_dest}/${CACHE_COMPLETION_MARKER}' ]; then
                echo '[cache-manager] tar extraction failed but existing valid cache found, reusing it' >&2
                exit 0
            fi
            echo '[cache-manager] ERROR: tar extraction failed and no existing valid cache' >&2
            exit 1
        fi
        extract_end=\$(date +%s.%N)
        extract_duration=\$(echo \"\$extract_end - \$extract_start\" | bc 2>/dev/null || echo '?')
        throughput=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / \$extract_duration\" | bc 2>/dev/null || echo '?')
        echo \"[cache-manager] Extraction completed in \${extract_duration}s (\${throughput} MB/s)\" >&2
    "; then
        _log "Cache ready"
    else
        _error "Failed to acquire lock or extract tar archive"
        # Clean up partial extraction, but preserve valid caches
        # See: HAF pipeline 150882 failure analysis
        if [[ -d "$local_dest" ]]; then
            if [[ -f "${local_dest}/${CACHE_COMPLETION_MARKER}" ]]; then
                _log "Extraction failed but existing valid cache preserved at: $local_dest"
            else
                _log "Cleaning up stale/partial extraction directory: $local_dest"
                sudo rm -rf "$local_dest" 2>/dev/null || rm -rf "$local_dest" 2>/dev/null || true
            fi
        fi
        return 1
    fi

    # Post-extraction fixes - run inside exclusive lock to prevent race conditions
    # where symlink modifications interfere with concurrent cp operations.
    # See: https://gitlab.syncad.com/hive/HAfAH/-/pipelines/150169 for the failure mode.
    #
    # Skip if cache was already complete (extracted by another job) - no fixes needed
    # and we may not have write permission to the directory created by another user/container
    if [[ -f "${local_dest}/${CACHE_COMPLETION_MARKER}" ]]; then
        _log "Cache extraction complete and verified"
        _update_lru "$cache_type" "$cache_key"
        return 0
    fi

    if _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$dest_lock" -c "
        # Link shared block_log for both hive and haf* caches (block_log files excluded from tar)
        case '$cache_type' in
            hive|haf*)
                # Create block_log symlinks if blockchain dir exists but is empty
                blockchain_dir='${local_dest}/datadir/blockchain'
                if [ -d \"\$blockchain_dir\" ] && [ -z \"\$(ls -A \"\$blockchain_dir\" 2>/dev/null)\" ]; then
                    for block_file in \"${SHARED_BLOCK_LOG_DIR:-/blockchain/block_log_5m}\"/block_log* ; do
                        if [ -f \"\$block_file\" ]; then
                            ln -sf \"\$block_file\" \"\$blockchain_dir/\$(basename \"\$block_file\")\" 2>/dev/null || true
                        fi
                    done
                    echo '[cache-manager] Linked shared block_log files' >&2
                fi
                ;;
        esac

        # Fix PostgreSQL permissions and symlinks for HAF caches
        case '$cache_type' in
            haf*)
                pgdata_path='${local_dest}/datadir/haf_db_store/pgdata'
                tablespace_path='${local_dest}/datadir/haf_db_store/tablespace'
                pg_tblspc='${local_dest}/datadir/haf_db_store/pgdata/pg_tblspc'

                # Fix pg_tblspc symlinks to use relative paths
                if [ -d \"\$pg_tblspc\" ]; then
                    for link in \"\$pg_tblspc\"/*; do
                        if [ -L \"\$link\" ]; then
                            target=\$(readlink \"\$link\")
                            # Only fix if absolute path (relative paths are already correct)
                            case \"\$target\" in
                                /*tablespace*)
                                    echo \"[cache-manager] Fixing pg_tblspc symlink: \$(basename \"\$link\")\" >&2
                                    sudo rm -f \"\$link\" 2>/dev/null || rm -f \"\$link\"
                                    sudo ln -s '../../tablespace' \"\$link\" 2>/dev/null || ln -s '../../tablespace' \"\$link\"
                                    ;;
                            esac
                        fi
                    done
                fi

                # Restore pgdata permissions
                if [ -d \"\$pgdata_path\" ]; then
                    sudo chmod 700 \"\$pgdata_path\" 2>/dev/null || chmod 700 \"\$pgdata_path\" 2>/dev/null || true
                    sudo chown -R 105:105 \"\$pgdata_path\" 2>/dev/null || true
                fi
                if [ -d \"\$tablespace_path\" ]; then
                    sudo chmod 700 \"\$tablespace_path\" 2>/dev/null || chmod 700 \"\$tablespace_path\" 2>/dev/null || true
                    sudo chown -R 105:105 \"\$tablespace_path\" 2>/dev/null || true
                fi
                ;;
        esac

        # Write completion marker to indicate extraction is fully complete
        # This must be the LAST step - if we get here, the cache is valid
        cat > '${local_dest}/${CACHE_COMPLETION_MARKER}' 2>/dev/null <<MARKER || true
timestamp=\$(date -Iseconds)
hostname=\$(hostname)
job_id=${CI_JOB_ID:-unknown}
pipeline_id=${CI_PIPELINE_ID:-unknown}
MARKER
        chmod 644 '${local_dest}/${CACHE_COMPLETION_MARKER}' 2>/dev/null || true
        echo '[cache-manager] Wrote completion marker' >&2
    "; then
        _log "Cache extraction complete and verified"
    else
        _error "Failed to apply post-extraction fixes"
        # Clean up incomplete extraction since it's not marked as complete
        if [[ -d "$local_dest" ]]; then
            _log "Cleaning up incomplete extraction: $local_dest"
            sudo rm -rf "$local_dest" 2>/dev/null || rm -rf "$local_dest" 2>/dev/null || true
        fi
        return 1
    fi

    _update_lru "$cache_type" "$cache_key"
    return 0
}

# PUT: Store cache as tar archive (NFS primary, local as fallback)
cmd_put() {
    local cache_type=""
    local cache_key=""
    local local_source=""
    local copy_from=""
    local shm_dir=""

    # Parse positional arguments and flags
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --copy-from)
                copy_from="$2"
                shift 2
                ;;
            --shm-dir)
                shm_dir="$2"
                shift 2
                ;;
            *)
                # Positional arguments: cache_type, cache_key, local_source
                if [[ -z "$cache_type" ]]; then
                    cache_type="$1"
                elif [[ -z "$cache_key" ]]; then
                    cache_key="$1"
                elif [[ -z "$local_source" ]]; then
                    local_source="$1"
                fi
                shift
                ;;
        esac
    done

    if [[ -z "$cache_type" ]] || [[ -z "$cache_key" ]] || [[ -z "$local_source" ]]; then
        _error "Usage: cache-manager put <cache_type> <cache_key> <local_dest> [--copy-from <datadir>] [--shm-dir <shm_dir>]"
        return 1
    fi

    # If --copy-from is provided, do a locked copy to local_source before tarring
    # This prevents race conditions when multiple jobs try to save to the same local cache
    if [[ -n "$copy_from" ]]; then
        if [[ ! -d "$copy_from" ]]; then
            _error "Copy source does not exist: $copy_from"
            return 1
        fi

        local dest_lock="${local_source}.lock"
        mkdir -p "$local_source"
        _touch_lock "$dest_lock"

        # Check for stale locks before attempting to acquire
        # Note: _check_stale_lock returns 1 if not stale, which would trigger errexit
        _check_stale_lock "$dest_lock" || true

        _log "Acquiring exclusive lock for local cache copy..."
        local copy_start=$(date +%s.%N)

        if ! _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$dest_lock" -c "
            lock_acquired=\$(date +%s.%N)
            echo \"[cache-manager] Exclusive lock acquired in \$(echo \"\$lock_acquired - $copy_start\" | bc 2>/dev/null || echo '?')s\" >&2

            # Re-check inside lock: another job may have finished the copy while we waited
            if [ -d '${local_source}/datadir/haf_db_store/pgdata' ]; then
                echo '[cache-manager] Cache already saved by another job, skipping copy' >&2
                exit 0
            fi

            # Clean up any partial/stale data from previous failed runs
            sudo rm -rf '${local_source}/datadir' '${local_source}/shm_dir' 2>/dev/null || rm -rf '${local_source}/datadir' '${local_source}/shm_dir' 2>/dev/null || true

            echo '[cache-manager] Copying datadir to local cache...' >&2
            sudo cp -aT '$copy_from' '${local_source}/datadir'

            # Copy shm_dir if provided
            if [ -n '$shm_dir' ] && [ -d '$shm_dir' ]; then
                echo '[cache-manager] Copying shm_dir to local cache...' >&2
                sudo cp -aT '$shm_dir' '${local_source}/shm_dir'
            fi

            # Remove empty blockchain directory to trigger symlink on test runners
            if [ -d '${local_source}/datadir/blockchain' ] && [ -z \"\$(ls -A '${local_source}/datadir/blockchain' 2>/dev/null)\" ]; then
                echo '[cache-manager] Removing empty blockchain directory from local cache' >&2
                rmdir '${local_source}/datadir/blockchain' 2>/dev/null || true
            fi
        "; then
            _error "Failed to acquire lock or copy data to local cache"
            return 1
        fi

        local copy_end=$(date +%s.%N)
        local copy_duration=$(echo "$copy_end - $copy_start" | bc 2>/dev/null || echo "?")
        _log "Local cache copy completed in ${copy_duration}s"
    fi

    if [[ ! -d "$local_source" ]]; then
        _error "Source directory does not exist: $local_source"
        return 1
    fi

    # For HAF caches: validate integrity and relax permissions
    # Covers: haf, haf_sync, haf_pipeline, haf_filtered, haf_btracker_sync, etc.
    if [[ "$cache_type" == haf* ]]; then
        # Validate pgdata integrity BEFORE caching to prevent propagating corruption
        if ! _validate_pgdata_integrity "$local_source"; then
            _error "Refusing to cache corrupted pgdata - fix PostgreSQL shutdown"
            return 1
        fi
        _relax_pgdata_permissions "$local_source"
    fi

    _get_paths "$cache_type" "$cache_key"

    local is_nfs_host=false
    _is_nfs_host && is_nfs_host=true

    # On NFS host, storage is local so no network I/O, but we still use tar format
    if [[ "$is_nfs_host" == "true" ]]; then
        # Check if already exists
        if [[ -f "$NFS_TAR_FILE" ]]; then
            _log "Cache already exists on NFS host, updating timestamp"
            _update_lru "$cache_type" "$cache_key"
            return 0
        fi

        # Build exclusions - exclude block_log files but preserve RocksDB directories
        local tar_excludes=""
        if [[ "$cache_type" == "hive" ]] || [[ "$cache_type" == haf* ]]; then
            tar_excludes=$(_build_blockchain_tar_excludes "$local_source")
        fi

        # Create tar archive (local I/O on NFS host, still fast)
        _log "Storing cache on NFS host: $NFS_TAR_FILE"
        _ensure_cache_dir "$(dirname "$NFS_TAR_FILE")"
        _touch_lock "$NFS_TAR_LOCK"

        # Check for stale locks before attempting to acquire
        # Note: _check_stale_lock returns 1 if not stale, which would trigger errexit
        _check_stale_lock "$NFS_TAR_LOCK" || true

        # shellcheck disable=SC2086
        if ! _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$NFS_TAR_LOCK" -c "
            tar cf '$NFS_TAR_FILE.tmp' $tar_excludes -C '$local_source' .
            mv '$NFS_TAR_FILE.tmp' '$NFS_TAR_FILE'
        "; then
            _error "Failed to store cache"
            return 1
        fi

        # Write metadata
        mkdir -p "$NFS_CACHE_DIR"
        _write_metadata "$cache_type" "$cache_key" "$local_source"
        _update_lru "$cache_type" "$cache_key"
        _log "Cache stored successfully on NFS host"
        _maybe_cleanup &
        return 0
    fi

    # NFS client path: create local tar first, then push to NFS

    # Check if already exists on NFS
    if _nfs_available && [[ -f "$NFS_TAR_FILE" ]]; then
        _log "Cache already exists on NFS, updating timestamp"
        # Ensure we have local copy too
        if [[ ! -f "$LOCAL_TAR_FILE" ]]; then
            mkdir -p "$(dirname "$LOCAL_TAR_FILE")"
            cp "$NFS_TAR_FILE" "$LOCAL_TAR_FILE" 2>/dev/null || true
        fi
        _update_lru "$cache_type" "$cache_key"
        return 0
    fi

    # Defense-in-depth: check if local tar already exists (concurrent job may have created it)
    if [[ -f "$LOCAL_TAR_FILE" ]]; then
        _log "Local cache already exists: $LOCAL_TAR_FILE"
        _update_lru "$cache_type" "$cache_key"
        return 0
    fi

    # Build exclusions - exclude block_log files but preserve RocksDB directories
    local tar_excludes=""
    if [[ "$cache_type" == "hive" ]] || [[ "$cache_type" == haf* ]]; then
        tar_excludes=$(_build_blockchain_tar_excludes "$local_source")
    fi

    # Step 1: Create local tar with exclusive lock to prevent race conditions
    # Race condition: two concurrent jobs can both pass the above check, then collide
    # on the .tmp file. Solution: use flock with double-check inside the lock.
    _log "Creating local cache: $LOCAL_TAR_FILE"
    mkdir -p "$(dirname "$LOCAL_TAR_FILE")"

    local tar_lock="${LOCAL_TAR_FILE}.lock"
    _touch_lock "$tar_lock"

    local tar_start=$(date +%s.%N 2>/dev/null || date +%s)
    # shellcheck disable=SC2086
    if ! _flock_with_timeout 600 -x "$tar_lock" -c "
        # Double-check inside lock: another job may have finished while we waited
        if [ -f '$LOCAL_TAR_FILE' ]; then
            echo '[cache-manager] Cache created by concurrent job while waiting for lock' >&2
            exit 0
        fi

        # Create tar with atomic rename
        tar cf '$LOCAL_TAR_FILE.tmp' $tar_excludes -C '$local_source' . || exit 1
        mv '$LOCAL_TAR_FILE.tmp' '$LOCAL_TAR_FILE' || exit 1
    "; then
        _error "Failed to create local tar"
        rm -f "$LOCAL_TAR_FILE.tmp"
        return 1
    fi

    # Log completion (if tar exists - we may have created it or another job did)
    if [[ -f "$LOCAL_TAR_FILE" ]]; then
        local tar_end=$(date +%s.%N 2>/dev/null || date +%s)
        local tar_duration=$(echo "$tar_end - $tar_start" | bc 2>/dev/null || echo "?")
        local tar_size=$(stat -c %s "$LOCAL_TAR_FILE" 2>/dev/null || echo 0)
        local tar_size_gb=$(echo "scale=2; $tar_size / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "?")
        _log "Local tar ready: ${tar_size_gb}GB (${tar_duration}s)"
    fi

    # Step 2: Push to NFS (if available)
    if ! _nfs_available; then
        _log "NFS not available, cached locally only"
        return 0
    fi

    _ensure_cache_dir "$(dirname "$NFS_TAR_FILE")"
    _touch_lock "$NFS_TAR_LOCK"

    # Check for stale locks before attempting to acquire
    # Note: _check_stale_lock returns 1 if not stale, which would trigger errexit
    _check_stale_lock "$NFS_TAR_LOCK" || true

    local lock_start_time=$(date +%s.%N)
    _log "Pushing to NFS: $NFS_TAR_FILE"

    if ! _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$NFS_TAR_LOCK" -c "
        # Write lock holder info for debugging
        cat > '${NFS_TAR_LOCK}.info' 2>/dev/null <<LOCKINFO || true
hostname=\$(hostname)
pid=\$\$
started=\$(date -Iseconds)
job_id=${CI_JOB_ID:-unknown}
pipeline_id=${CI_PIPELINE_ID:-unknown}
LOCKINFO

        # Double-check after acquiring lock (another job may have pushed while we waited)
        if [ -f '$NFS_TAR_FILE' ]; then
            echo '[cache-manager] Cache was created while waiting for lock' >&2
            exit 0
        fi

        # Copy local tar to NFS
        copy_start=\$(date +%s.%N)
        cp '$LOCAL_TAR_FILE' '$NFS_TAR_FILE.tmp'
        mv '$NFS_TAR_FILE.tmp' '$NFS_TAR_FILE'
        copy_end=\$(date +%s.%N)

        copy_duration=\$(echo \"\$copy_end - \$copy_start\" | bc 2>/dev/null || echo '?')
        throughput=\$(echo \"scale=2; $tar_size / 1024 / 1024 / \$copy_duration\" | bc 2>/dev/null || echo '?')
        echo \"[cache-manager] NFS push completed in \${copy_duration}s (\${throughput} MB/s)\" >&2

        # Clean up lock info file
        rm -f '${NFS_TAR_LOCK}.info' 2>/dev/null || true
    "; then
        _error "Failed to push to NFS (local cache exists but won't help cross-builder jobs)"
        return 1
    fi

    # Write metadata next to tar file (if NFS push succeeded)
    if [[ -f "$NFS_TAR_FILE" ]]; then
        local TAR_METADATA="${NFS_TAR_FILE%.tar}/.metadata"
        mkdir -p "$(dirname "$TAR_METADATA")"
        _write_metadata "$cache_type" "$cache_key" "$local_source"
        mv "$METADATA_FILE" "$TAR_METADATA" 2>/dev/null || true
        _update_lru "$cache_type" "$cache_key"
    fi

    _log "Cache stored successfully"

    # Trigger async cleanup check
    _maybe_cleanup &

    return 0
}

# CLEANUP: Remove old caches using LRU eviction
cmd_cleanup() {
    local cache_type="${1:-}"
    local max_size_gb="$CACHE_MAX_SIZE_GB"
    local max_age_days="$CACHE_MAX_AGE_DAYS"

    # Parse options
    shift || true
    while [[ $# -gt 0 ]]; do
        case $1 in
            --max-size-gb)
                max_size_gb="$2"
                shift 2
                ;;
            --max-age-days)
                max_age_days="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    if ! _nfs_available; then
        _error "NFS not available for cleanup"
        return 1
    fi

    _log "Starting cleanup (max_size=${max_size_gb}GB, max_age=${max_age_days}days)"

    # Clean up stale lock files first
    _cleanup_stale_locks "$CACHE_NFS_PATH"

    local max_size_bytes=$((max_size_gb * 1024 * 1024 * 1024))
    local cutoff_timestamp=$(($(date +%s) - max_age_days * 86400))

    local lru_index="${CACHE_NFS_PATH}/.lru_index"

    # Calculate current total size
    # Resolve symlinks - du on a symlink returns symlink size, not target size
    local search_path
    search_path=$(readlink -f "$CACHE_NFS_PATH")
    [[ -n "$cache_type" ]] && search_path="${search_path}/$cache_type"

    local total_size
    total_size=$(du -sb "$search_path" 2>/dev/null | awk '{print $1}' | head -1) || true
    [[ -z "$total_size" || ! "$total_size" =~ ^[0-9]+$ ]] && total_size=0
    _log "Current cache size: $((total_size / 1024 / 1024 / 1024))GB"

    # Sort by timestamp (oldest first) and process LRU entries
    local removed=0
    if [[ ! -f "$lru_index" ]]; then
        _log "No LRU index found, skipping LRU cleanup (will still check for orphans)"
    else
    while IFS='|' read -r timestamp entry; do
        # Skip if filtering by type and doesn't match
        if [[ -n "$cache_type" && ! "$entry" =~ ^${cache_type}/ ]]; then
            continue
        fi

        local entry_dir="$CACHE_NFS_PATH/$entry"
        local entry_tar="${entry_dir}.tar"
        local entry_tar_lock="${entry_tar}.lock"

        # Skip if doesn't exist (tar file is the primary format)
        [[ -f "$entry_tar" ]] || continue

        # Skip entries created in the last 5 minutes (protect recently cached data)
        local min_age_seconds=300
        local current_time=$(date +%s)
        if [[ $((current_time - timestamp)) -lt $min_age_seconds ]]; then
            _log "Skipping $entry - created less than 5 minutes ago"
            continue
        fi

        # Check if should remove (age or size)
        local should_remove=false

        if [[ $timestamp -lt $cutoff_timestamp ]]; then
            _log "Entry $entry is older than ${max_age_days} days"
            should_remove=true
        elif [[ $total_size -gt $max_size_bytes ]]; then
            _log "Total size exceeds limit, removing oldest: $entry"
            should_remove=true
        fi

        if [[ "$should_remove" == "true" ]]; then
            # Check if locked (skip if in use)
            if [[ -f "$entry_tar_lock" ]] && ! flock -n "$entry_tar_lock" -c "true" 2>/dev/null; then
                _log "Skipping $entry - currently locked"
                continue
            fi

            local entry_size=$(stat -c %s "$entry_tar" 2>/dev/null || echo 0)
            _log "Removing: $entry (${entry_size} bytes)"
            # Use sudo to remove files owned by different container UIDs
            sudo rm -f "$entry_tar" "$entry_tar_lock" "${entry_tar_lock}.info" 2>/dev/null || \
                rm -f "$entry_tar" "$entry_tar_lock" "${entry_tar_lock}.info" 2>/dev/null || true
            sudo rm -rf "$entry_dir" 2>/dev/null || rm -rf "$entry_dir" 2>/dev/null || true
            total_size=$((total_size - entry_size))
            removed=$((removed + 1))

            # Remove from LRU index (with locking to prevent race with _update_lru)
            local global_lock="${CACHE_NFS_PATH}/.global_lock"
            _touch_lock "$global_lock"
            _flock_with_timeout 30 -x "$global_lock" -c "
                if [ -f '$lru_index' ]; then
                    grep -v '|${entry}\$' '$lru_index' > '${lru_index}.tmp' 2>/dev/null || true
                    # Only mv if tmp file exists and has content (avoid clobbering with empty file)
                    if [ -s '${lru_index}.tmp' ]; then
                        mv '${lru_index}.tmp' '$lru_index'
                    elif [ -f '${lru_index}.tmp' ]; then
                        # tmp is empty, meaning we removed the last entry
                        mv '${lru_index}.tmp' '$lru_index'
                    fi
                fi
            " || _log "WARNING: Failed to update LRU index for removed entry"
        fi

        # Stop if under limit
        [[ $total_size -le $max_size_bytes ]] && break

    done < <(sort -t'|' -k1 -n "$lru_index")

        _log "LRU cleanup complete, removed $removed entries"
    fi

    # Phase 2: Clean orphaned tar files not in LRU index
    # These are files created before LRU tracking was implemented, or that failed to be indexed
    _log "Scanning for orphaned tar files not in LRU index..."

    local orphan_removed=0
    local cutoff_mtime=$(($(date +%s) - max_age_days * 86400))

    # Build list of LRU entries for fast lookup
    local lru_entries_file
    lru_entries_file=$(mktemp)
    if [[ -f "$lru_index" ]]; then
        grep -oP '\|.*$' "$lru_index" 2>/dev/null | sed 's/^|//' | sort > "$lru_entries_file"
    fi

    # Find all tar files and check if they're in the LRU index
    while IFS= read -r tar_path; do
        [[ -f "$tar_path" ]] || continue

        # Extract cache_type/cache_key from path (e.g., ./haf_filtered/146072_filtered.tar -> haf_filtered/146072_filtered)
        local entry
        entry=$(echo "$tar_path" | sed "s|^${search_path}/||; s|\.tar$||")

        # Skip if filtering by type and doesn't match
        if [[ -n "$cache_type" && ! "$entry" =~ ^${cache_type}/ ]]; then
            continue
        fi

        # Skip if in LRU index (already handled above)
        if grep -qF "$entry" "$lru_entries_file" 2>/dev/null; then
            continue
        fi

        # Get file mtime
        local file_mtime
        file_mtime=$(stat -c %Y "$tar_path" 2>/dev/null || echo 0)

        # Skip files newer than max_age_days
        if [[ $file_mtime -gt $cutoff_mtime ]]; then
            continue
        fi

        # Skip if locked
        local tar_lock="${tar_path}.lock"
        if [[ -f "$tar_lock" ]] && ! flock -n "$tar_lock" -c "true" 2>/dev/null; then
            _log "Skipping orphan $entry - currently locked"
            continue
        fi

        # Remove the orphaned tar file
        local file_size
        file_size=$(stat -c %s "$tar_path" 2>/dev/null || echo 0)
        local age_days=$(( ($(date +%s) - file_mtime) / 86400 ))
        _log "Removing orphan: $entry (${age_days} days old, $((file_size / 1024 / 1024 / 1024))GB)"

        sudo rm -f "$tar_path" "$tar_lock" "${tar_lock}.info" 2>/dev/null || \
            rm -f "$tar_path" "$tar_lock" "${tar_lock}.info" 2>/dev/null || true

        # Also remove metadata directory if exists
        local meta_dir="${tar_path%.tar}"
        [[ -d "$meta_dir" ]] && { sudo rm -rf "$meta_dir" 2>/dev/null || rm -rf "$meta_dir" 2>/dev/null || true; }

        total_size=$((total_size - file_size))
        orphan_removed=$((orphan_removed + 1))

        # Stop if under size limit
        [[ $total_size -le $max_size_bytes ]] && break

    done < <(find "$search_path" -name "*.tar" -type f 2>/dev/null | sort)

    rm -f "$lru_entries_file"

    if [[ $orphan_removed -gt 0 ]]; then
        _log "Orphan cleanup complete, removed $orphan_removed orphaned files"
    fi

    _log "Total cleanup: $((removed + orphan_removed)) entries removed"
}

# Cleanup local cache when it exceeds size limit
# Removes oldest tar files (by mtime) until under threshold
_cleanup_local_cache() {
    local max_size_gb="${1:-$CACHE_LOCAL_MAX_GB}"

    # Skip if local cache directory doesn't exist
    if [[ ! -d "$CACHE_LOCAL_PATH" ]]; then
        return 0
    fi

    # Skip on NFS host (local IS NFS, managed by cmd_cleanup)
    if _is_nfs_host; then
        return 0
    fi

    # Resolve symlinks - du on a symlink returns symlink size, not target size
    local real_cache_path
    real_cache_path=$(readlink -f "$CACHE_LOCAL_PATH")

    local max_size_bytes=$((max_size_gb * 1024 * 1024 * 1024))

    # Calculate current total size
    # Note: du may return non-zero exit code due to permission errors while still
    # outputting valid size. Don't use || pattern with pipefail - just validate result.
    local total_size
    total_size=$(du -sb "$real_cache_path" 2>/dev/null | awk '{print $1}') || true
    [[ -z "$total_size" || ! "$total_size" =~ ^[0-9]+$ ]] && total_size=0

    local total_size_gb=$((total_size / 1024 / 1024 / 1024))
    _log "Local cache size: ${total_size_gb}GB (max: ${max_size_gb}GB)"

    if [[ $total_size -le $max_size_bytes ]]; then
        _log "Local cache under limit, no cleanup needed"
        return 0
    fi

    _log "Local cache over limit, starting cleanup"

    # Find all tar files, sorted by mtime (oldest first)
    local removed=0
    while IFS= read -r tarfile; do
        [[ -f "$tarfile" ]] || continue

        # Skip files currently being written (.tmp suffix)
        if [[ -f "${tarfile}.tmp" ]]; then
            _log "Skipping $tarfile - write in progress (.tmp exists)"
            continue
        fi

        # Skip if copylock exists (another job is copying from NFS)
        if [[ -f "${tarfile}.copylock" ]]; then
            local lock_file="${tarfile}.copylock"
            # Check if lock is actually held
            if ! flock -n "$lock_file" -c "true" 2>/dev/null; then
                _log "Skipping $tarfile - copy in progress"
                continue
            fi
        fi

        # Skip if lock exists and is held (extraction in progress)
        if [[ -f "${tarfile}.lock" ]]; then
            local lock_file="${tarfile}.lock"
            if ! flock -n "$lock_file" -c "true" 2>/dev/null; then
                _log "Skipping $tarfile - currently locked (extraction in progress)"
                continue
            fi
        fi

        local file_size=$(stat -c %s "$tarfile" 2>/dev/null || echo 0)
        local file_size_gb=$((file_size / 1024 / 1024 / 1024))
        local filename=$(basename "$tarfile")

        _log "Removing oldest local cache: $filename (${file_size_gb}GB)"
        # Use sudo to remove files owned by different container UIDs (4000, 2000, etc.)
        sudo rm -f "$tarfile" "${tarfile}.lock" "${tarfile}.copylock" 2>/dev/null || \
            rm -f "$tarfile" "${tarfile}.lock" "${tarfile}.copylock" 2>/dev/null || true
        total_size=$((total_size - file_size))
        removed=$((removed + 1))

        # Stop if under limit
        if [[ $total_size -le $max_size_bytes ]]; then
            break
        fi

    done < <(find "$real_cache_path" -maxdepth 1 -name "*.tar" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | cut -d' ' -f2-)

    local final_size_gb=$((total_size / 1024 / 1024 / 1024))
    _log "Local cleanup complete: removed $removed files, new size: ${final_size_gb}GB"
}

# Clean up stale directories in local cache
# Removes directories older than max_age_days. This includes:
#   - Orphan directories (no corresponding tar file)
#   - Stale extracted directories (have tar file but are old working copies)
# The tar file is the source of truth; directories are just working copies.
# Returns number of directories removed (or that would be removed in dry-run mode).
_cleanup_orphan_directories() {
    local max_age_days="${1:-7}"
    local cache_path="${2:-$CACHE_LOCAL_PATH}"
    local dry_run="${3:-false}"
    local removed=0

    # Skip if cache path doesn't exist
    if [[ ! -d "$cache_path" ]]; then
        _log "Cache path does not exist: $cache_path"
        return 0
    fi

    # Resolve symlinks for consistent path handling
    local real_cache_path
    real_cache_path=$(readlink -f "$cache_path")

    _log "Scanning for stale directories in: $real_cache_path (older than ${max_age_days} days)"

    # Find directories older than max_age_days
    # Using -mtime +N finds files modified MORE than N days ago
    while IFS= read -r dir; do
        [[ -d "$dir" ]] || continue

        local base
        base=$(basename "$dir")

        # Skip known non-cache directories
        case "$base" in
            blockchain|block_log_5m|block_log_5m_mirrornet|logs|tmp|.*)
                continue
                ;;
        esac

        # Note: We intentionally do NOT skip directories with tar files.
        # The tar file is the source of truth. Old extracted directories should
        # be cleaned up regardless - they can be re-extracted from the tar if needed.
        # This is critical for preventing disk space exhaustion on builders.

        # Get directory size (use sudo to read postgres-owned subdirectories)
        local dir_size
        dir_size=$(sudo du -sb "$dir" 2>/dev/null | awk '{print $1}') || dir_size=0
        local dir_size_gb
        dir_size_gb=$(echo "scale=2; ${dir_size:-0} / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "?")

        # Check if tar file exists (for logging purposes)
        local has_tar="orphan"
        if [[ -f "${real_cache_path}/${base}.tar" ]]; then
            has_tar="stale extraction"
        fi

        if [[ "$dry_run" == "true" ]]; then
            echo "Would remove ${has_tar}: $base (${dir_size_gb}GB)" >&2
        else
            _log "Removing ${has_tar}: $base (${dir_size_gb}GB)"
            # Use sudo for directories that may be owned by postgres (UID 105) or other container UIDs
            if sudo rm -rf "$dir" 2>/dev/null || rm -rf "$dir" 2>/dev/null; then
                _log "Removed: $base"
            else
                _error "Failed to remove: $base"
                continue
            fi
        fi
        removed=$((removed + 1))

    done < <(find "$real_cache_path" -maxdepth 1 -type d -mtime "+${max_age_days}" 2>/dev/null)

    if [[ "$dry_run" == "true" ]]; then
        echo "Would remove $removed stale directories" >&2
    else
        _log "Removed $removed stale directories"
    fi

    # Return count on stdout (for capture by caller)
    echo "$removed"
}

# Command wrapper for manual local cleanup
cmd_cleanup_local() {
    local max_size_gb="$CACHE_LOCAL_MAX_GB"

    # Parse options
    while [[ $# -gt 0 ]]; do
        case $1 in
            --max-size-gb)
                max_size_gb="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    _cleanup_local_cache "$max_size_gb"
}

# Command wrapper for stale directory cleanup (renamed from orphan cleanup)
# Removes both orphan directories AND old extracted directories with tar files.
# Usage: cache-manager.sh cleanup-orphans [--max-age-days N] [--dry-run]
cmd_cleanup_orphans() {
    local max_age_days=7
    local dry_run=false

    # Parse options
    while [[ $# -gt 0 ]]; do
        case $1 in
            --max-age-days)
                max_age_days="$2"
                shift 2
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    # Resolve symlinks for accurate size calculation
    local real_cache_path
    real_cache_path=$(readlink -f "$CACHE_LOCAL_PATH" 2>/dev/null || echo "$CACHE_LOCAL_PATH")

    echo "=== Stale Directory Cleanup ==="
    echo "Cache path:    $CACHE_LOCAL_PATH"
    [[ "$real_cache_path" != "$CACHE_LOCAL_PATH" ]] && echo "Resolved path: $real_cache_path"
    echo "Max age:       $max_age_days days"
    echo "Dry run:       $dry_run"
    echo ""

    # Get initial size (use sudo to read postgres-owned directories accurately)
    local initial_size
    initial_size=$(sudo du -sb "$real_cache_path" 2>/dev/null | awk '{print $1}') || initial_size=0
    [[ -z "$initial_size" || ! "$initial_size" =~ ^[0-9]+$ ]] && initial_size=0
    local initial_size_gb
    initial_size_gb=$(echo "scale=2; ${initial_size:-0} / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "?")
    echo "Initial cache size: ${initial_size_gb}GB"
    echo ""

    local removed
    removed=$(_cleanup_orphan_directories "$max_age_days" "$CACHE_LOCAL_PATH" "$dry_run")

    echo ""

    if [[ "$dry_run" != "true" ]] && [[ "$removed" -gt 0 ]]; then
        # Get final size (use sudo for accurate size)
        local final_size
        final_size=$(sudo du -sb "$real_cache_path" 2>/dev/null | awk '{print $1}') || final_size=0
        [[ -z "$final_size" || ! "$final_size" =~ ^[0-9]+$ ]] && final_size=0
        local final_size_gb
        final_size_gb=$(echo "scale=2; ${final_size:-0} / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "?")
        local freed_gb
        freed_gb=$(echo "scale=2; (${initial_size:-0} - ${final_size:-0}) / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "?")
        echo "Final cache size: ${final_size_gb}GB (freed ${freed_gb}GB)"
    fi
}

# Maybe trigger cleanup if size is getting large (both local and NFS)
_maybe_cleanup() {
    # Check local cache first (independent of NFS)
    if [[ -d "$CACHE_LOCAL_PATH" ]] && ! _is_nfs_host; then
        # Resolve symlinks - du on a symlink returns symlink size, not target size
        local real_local_path
        real_local_path=$(readlink -f "$CACHE_LOCAL_PATH")

        local local_size
        local_size=$(du -sb "$real_local_path" 2>/dev/null | awk '{print $1}') || local_size=0
        [[ -z "$local_size" || ! "$local_size" =~ ^[0-9]+$ ]] && local_size=0
        local local_max_bytes=$((CACHE_LOCAL_MAX_GB * 1024 * 1024 * 1024))
        local local_threshold=$((local_max_bytes * 90 / 100))  # 90% threshold

        if [[ $local_size -gt $local_threshold ]]; then
            _log "Local cache at 90% capacity, triggering local cleanup"
            _cleanup_local_cache "$CACHE_LOCAL_MAX_GB"
            # Also clean orphan directories (more aggressive when at high capacity)
            _cleanup_orphan_directories 3 "$CACHE_LOCAL_PATH" false >/dev/null
        fi
    fi

    # Check NFS cache
    if ! _nfs_available; then
        return 0
    fi

    # Resolve symlinks - du on a symlink returns symlink size, not target size
    local real_nfs_path
    real_nfs_path=$(readlink -f "$CACHE_NFS_PATH")

    local total_size
    total_size=$(du -sb "$real_nfs_path" 2>/dev/null | awk '{print $1}') || true
    [[ -z "$total_size" || ! "$total_size" =~ ^[0-9]+$ ]] && total_size=0
    local max_bytes=$((CACHE_MAX_SIZE_GB * 1024 * 1024 * 1024))
    local threshold=$((max_bytes * 90 / 100))  # 90% threshold

    if [[ "$total_size" =~ ^[0-9]+$ ]] && [[ $total_size -gt $threshold ]]; then
        _log "NFS cache at 90% capacity, triggering cleanup"
        cmd_cleanup "" --max-size-gb "$CACHE_MAX_SIZE_GB" --max-age-days "$CACHE_MAX_AGE_DAYS"
    fi
}

# LIST: Show caches of a given type
cmd_list() {
    local cache_type="${1:-}"

    echo "=== Local Caches (${CACHE_LOCAL_PATH}) ==="
    local pattern="${CACHE_LOCAL_PATH}/${cache_type}*.tar"
    for tarfile in $pattern; do
        [[ -f "$tarfile" ]] || continue
        local size=$(du -sh "$tarfile" 2>/dev/null | cut -f1 || echo "?")
        local mtime=$(stat -c %y "$tarfile" 2>/dev/null | cut -d. -f1 || echo "?")
        local key=$(basename "$tarfile" .tar)
        echo "  $key - ${size} - ${mtime}"
    done

    if _nfs_available; then
        echo ""
        echo "=== NFS Caches (${CACHE_NFS_PATH}) ==="
        local nfs_path="$CACHE_NFS_PATH"
        [[ -n "$cache_type" ]] && nfs_path="$CACHE_NFS_PATH/$cache_type"

        if [[ -d "$nfs_path" ]]; then
            # List tar archives (current format)
            for tarfile in "$nfs_path"/*.tar; do
                [[ -f "$tarfile" ]] || continue
                local size=$(du -sh "$tarfile" 2>/dev/null | cut -f1 || echo "?")
                local key=$(basename "$tarfile" .tar)
                local mtime=$(stat -c %y "$tarfile" 2>/dev/null | cut -d. -f1 || echo "?")
                local meta_dir="${tarfile%.tar}"
                local meta=""
                if [[ -f "$meta_dir/.metadata" ]]; then
                    meta=$(jq -r '.created_at // "?"' "$meta_dir/.metadata" 2>/dev/null || echo "?")
                else
                    meta="$mtime"
                fi
                echo "  $key - ${size} - ${meta}"
            done
        fi
    else
        echo ""
        echo "=== NFS not available ==="
    fi
}

# IS-FAST-BUILDER: Check if running on a fast builder (5950 CPU)
cmd_is_fast_builder() {
    # Check CPU model
    local cpu_model=$(cat /proc/cpuinfo 2>/dev/null | grep "model name" | head -1 || echo "")

    if echo "$cpu_model" | grep -qE "5950|5900|EPYC"; then
        _log "Fast builder detected: $cpu_model"
        return 0
    fi

    # Fallback: check hostname
    local hostname=$(hostname)
    case "$hostname" in
        hive-builder-8|hive-builder-9|hive-builder-10)
            _log "Fast builder (by hostname): $hostname"
            return 0
            ;;
    esac

    _log "Not a fast builder: ${cpu_model:-unknown CPU}"
    return 1
}

# STATUS: Show overall cache status
cmd_status() {
    echo "Cache Manager Status"
    echo "===================="
    echo "NFS Path:       $CACHE_NFS_PATH"
    echo "Local Path:     $CACHE_LOCAL_PATH"
    echo "NFS Max Size:   ${CACHE_MAX_SIZE_GB}GB"
    echo "Local Max Size: ${CACHE_LOCAL_MAX_GB}GB"
    echo "Max Age:        ${CACHE_MAX_AGE_DAYS} days"
    echo "NFS Host:       $(_is_nfs_host && echo "YES (local storage)" || echo "NO (NFS client)")"
    echo ""

    local lru_index="${CACHE_NFS_PATH}/.lru_index"

    if _nfs_available; then
        echo "NFS Status:   AVAILABLE"
        # Resolve symlinks - du on a symlink returns symlink size, not target size
        local real_nfs_path
        real_nfs_path=$(readlink -f "$CACHE_NFS_PATH")
        local total=$(du -sh "$real_nfs_path" 2>/dev/null | cut -f1 || echo "?")
        echo "NFS Usage:    $total / ${CACHE_MAX_SIZE_GB}GB"

        if [[ -f "$lru_index" ]]; then
            local count=$(wc -l < "$lru_index")
            echo "Cache Entries: $count"
        fi
    else
        echo "NFS Status:   NOT AVAILABLE"
    fi

    echo ""
    echo "Local Cache:"
    if [[ -d "$CACHE_LOCAL_PATH" ]]; then
        # Resolve symlinks - du on a symlink returns symlink size, not target size
        local real_cache_path
        real_cache_path=$(readlink -f "$CACHE_LOCAL_PATH")
        # Note: du may return non-zero due to permission errors while still outputting valid size
        local local_size=$(du -sb "$real_cache_path" 2>/dev/null | awk '{print $1}') || true
        [[ -z "$local_size" || ! "$local_size" =~ ^[0-9]+$ ]] && local_size=0
        local local_size_gb=$((local_size / 1024 / 1024 / 1024))
        local local_count=$(find "$real_cache_path" -maxdepth 1 -name "*.tar" -type f 2>/dev/null | wc -l)
        echo "  Usage: ${local_size_gb}GB / ${CACHE_LOCAL_MAX_GB}GB (${local_count} files)"
        if _is_nfs_host; then
            echo "  (Local cleanup disabled - NFS host)"
        fi
        echo "  Files:"
        du -sh "${CACHE_LOCAL_PATH}"/*.tar 2>/dev/null | head -10 || echo "    (none)"
    else
        echo "  (directory not found)"
    fi
}

# Main
usage() {
    head -21 "$0" | tail -19 | sed 's/^# //'
    exit 1
}

[[ $# -lt 1 ]] && usage

cmd="$1"
shift

case "$cmd" in
    get)
        [[ $# -lt 3 ]] && { _error "get requires: <cache-type> <cache-key> <local-dest>"; exit 1; }
        _check_flock_support
        cmd_get "$@"
        ;;
    put)
        [[ $# -lt 3 ]] && { _error "put requires: <cache-type> <cache-key> <local-source>"; exit 1; }
        _check_flock_support
        cmd_put "$@"
        ;;
    cleanup)
        _check_flock_support
        cmd_cleanup "$@"
        ;;
    cleanup-local)
        cmd_cleanup_local "$@"
        ;;
    cleanup-orphans)
        cmd_cleanup_orphans "$@"
        ;;
    list)
        cmd_list "$@"
        ;;
    status)
        cmd_status
        ;;
    is-fast-builder)
        cmd_is_fast_builder
        ;;
    *)
        _error "Unknown command: $cmd"
        usage
        ;;
esac
