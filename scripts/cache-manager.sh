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
#   CACHE_NFS_PATH      - NFS mount point (default: /nfs/ci-cache)
#   CACHE_LOCAL_PATH    - Local cache directory (default: /cache)
#   CACHE_MAX_SIZE_GB   - Max total NFS cache size (default: 2000)
#   CACHE_MAX_AGE_DAYS  - Max cache age (default: 30)
#   CACHE_LOCK_TIMEOUT  - Lock timeout in seconds (default: 3600)
#   CACHE_QUIET         - Suppress verbose output (default: false)

set -euo pipefail

# Detect flock capabilities (BusyBox vs GNU coreutils)
# BusyBox flock doesn't support -w (timeout), only -n (nonblock)
_flock_supports_timeout() {
    flock --help 2>&1 | grep -q -- '-w' 2>/dev/null
}

# Wrapper for flock that handles BusyBox compatibility
# Usage: _flock_with_timeout <timeout> <mode> <lockfile> <command...>
#   mode: -s (shared) or -x (exclusive)
_flock_with_timeout() {
    local timeout="$1"
    local mode="$2"
    local lockfile="$3"
    shift 3

    if _flock_supports_timeout; then
        # GNU coreutils flock - use -w for timeout
        flock "$mode" -w "$timeout" "$lockfile" "$@"
    else
        # BusyBox flock - no timeout support, use -n (non-blocking) with retry loop
        local elapsed=0
        local interval=5
        while [[ $elapsed -lt $timeout ]]; do
            if flock "$mode" -n "$lockfile" "$@" 2>/dev/null; then
                return 0
            fi
            sleep "$interval"
            elapsed=$((elapsed + interval))
        done
        _error "Timeout waiting for lock after ${timeout}s"
        return 1
    fi
}

# Configuration with defaults
CACHE_NFS_PATH="${CACHE_NFS_PATH:-/nfs/ci-cache}"
CACHE_LOCAL_PATH="${CACHE_LOCAL_PATH:-/cache}"
CACHE_MAX_SIZE_GB="${CACHE_MAX_SIZE_GB:-2000}"
CACHE_MAX_AGE_DAYS="${CACHE_MAX_AGE_DAYS:-30}"
CACHE_LOCK_TIMEOUT="${CACHE_LOCK_TIMEOUT:-120}"  # 2 minutes (NFS writes take ~10s, 12x margin)
CACHE_STALE_LOCK_MINUTES="${CACHE_STALE_LOCK_MINUTES:-10}"  # Break locks older than this (writes take ~10s)
CACHE_QUIET="${CACHE_QUIET:-false}"

# Logging
_log() {
    if [[ "$CACHE_QUIET" != "true" ]]; then
        echo "[cache-manager] $1" >&2
    fi
}

_error() {
    echo "[cache-manager] ERROR: $1" >&2
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

    [[ $cleaned -gt 0 ]] && _log "Cleaned up $cleaned stale lock files"
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

    # On NFS host, use NFS path as local path to avoid redundant copies
    if _is_nfs_host; then
        LOCAL_CACHE_DIR="$NFS_CACHE_DIR"
    else
        LOCAL_CACHE_DIR="${CACHE_LOCAL_PATH}/${cache_type}_${cache_key}"
    fi

    LOCK_FILE="${NFS_CACHE_DIR}/.lock"
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
    touch "$GLOBAL_LOCK"
    _flock_with_timeout 30 -x "$GLOBAL_LOCK" -c "
        # Create or update LRU index (simple format: timestamp|path per line)
        if [[ -f '$LRU_INDEX' ]]; then
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

# Build tar exclusion arguments for HAF caches to reduce size
# Excludes: blockchain (use shared block_log)
# NOTE: We keep ALL WAL files to ensure safe PostgreSQL recovery.
# Previously we tried to exclude WAL files except the checkpoint WAL to save ~5.8GB,
# but this caused data corruption when PostgreSQL started crash recovery on extracted data.
# The tar may be created while PostgreSQL is still running (docker-compose down takes time),
# so we need all WAL files for proper recovery.
_build_haf_tar_excludes() {
    local source_dir="$1"
    local excludes=""

    # Exclude blockchain directory - tests should use /nfs/ci-cache/hive/block_log_5m
    # Saves ~1.7GB
    if [[ -d "${source_dir}/datadir/blockchain" ]]; then
        excludes="--exclude=./datadir/blockchain"
        _log "Excluding datadir/blockchain (use shared block_log instead)"
    fi

    # Keep all pg_wal files - required for safe PostgreSQL recovery

    echo "$excludes"
}

# GET: Check local, then NFS, copy to local if found on NFS
cmd_get() {
    local cache_type="$1"
    local cache_key="$2"
    local local_dest="$3"

    _get_paths "$cache_type" "$cache_key"

    local is_nfs_host=false
    _is_nfs_host && is_nfs_host=true

    # 1. Check local cache first (on NFS host, this IS the NFS cache)
    if [[ -d "$LOCAL_CACHE_DIR" ]]; then
        _log "Cache hit: $LOCAL_CACHE_DIR"
        if [[ "$LOCAL_CACHE_DIR" != "$local_dest" ]]; then
            _log "Copying to destination: $local_dest"
            mkdir -p "$(dirname "$local_dest")"
            # Use cp -r instead of cp -a to avoid permission issues on NFS
            # (cp -a tries to preserve ownership which can fail on NFS)
            cp -r "$LOCAL_CACHE_DIR" "$local_dest"
        else
            _log "Destination is cache dir, no copy needed"
        fi
        # Restore pgdata permissions for HAF caches
        if [[ "$cache_type" == "haf" ]]; then
            _restore_pgdata_permissions "$local_dest"
        fi
        # Update LRU if NFS available
        if _nfs_available; then
            _update_lru "$cache_type" "$cache_key" || true
        fi
        return 0
    fi

    # On NFS host, local and NFS are the same - if local miss, it's a miss
    if [[ "$is_nfs_host" == "true" ]]; then
        _log "NFS host cache miss: $NFS_CACHE_DIR"
        return 1
    fi

    # 2. Check NFS cache (only for NFS clients)
    if ! _nfs_available; then
        _log "NFS not available, cache miss"
        return 1
    fi

    # Check for tar archive first (new format), then directory (legacy format)
    local NFS_TAR_FILE="${NFS_CACHE_DIR}.tar"
    local use_tar=false

    if [[ -f "$NFS_TAR_FILE" ]]; then
        use_tar=true
        _log "NFS cache hit (tar archive): $NFS_TAR_FILE"
    elif [[ -d "$NFS_CACHE_DIR" ]]; then
        _log "NFS cache hit (directory): $NFS_CACHE_DIR"
    else
        _log "NFS cache miss: $NFS_CACHE_DIR (no tar or dir)"
        return 1
    fi

    # 3. Copy from NFS to local - NFS clients only
    mkdir -p "$local_dest"

    if [[ "$use_tar" == "true" ]]; then
        # Extract tar archive to local (fast: reading single file from NFS)
        local NFS_TAR_LOCK="${NFS_TAR_FILE}.lock"
        touch "$NFS_TAR_LOCK" 2>/dev/null || true

        local get_start_time=$(date +%s.%N)
        if _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -s "$NFS_TAR_LOCK" -c "
            lock_acquired=\$(date +%s.%N)
            echo \"[cache-manager] Shared lock acquired in \$(echo \"\$lock_acquired - $get_start_time\" | bc)s\" >&2

            tar_size=\$(stat -c %s '$NFS_TAR_FILE' 2>/dev/null || echo 0)
            tar_size_gb=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / 1024\" | bc)
            echo \"[cache-manager] Extracting tar archive (\${tar_size_gb}GB) to local: $local_dest\" >&2

            extract_start=\$(date +%s.%N)
            tar xf '$NFS_TAR_FILE' -C '$local_dest'
            extract_end=\$(date +%s.%N)
            extract_duration=\$(echo \"\$extract_end - \$extract_start\" | bc)
            throughput=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / \$extract_duration\" | bc 2>/dev/null || echo '?')
            echo \"[cache-manager] Extraction completed in \${extract_duration}s (\${throughput} MB/s)\" >&2
        "; then
            _log "Extracted tar archive successfully"
        else
            _error "Failed to extract tar archive"
            return 1
        fi
    else
        # Legacy directory format - use tar pipe for faster reads
        mkdir -p "$(dirname "$LOCK_FILE")"
        touch "$LOCK_FILE"

        if _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -s "$LOCK_FILE" -c "
            echo '[cache-manager] Copying from NFS directory to local: $local_dest' >&2
            (cd '$NFS_CACHE_DIR' && tar cf - .) | (cd '$local_dest' && tar xf -)
        "; then
            _log "Copied from directory successfully"
        else
            _error "Failed to acquire shared lock"
            return 1
        fi
    fi

    # Cache locally for future use (symlink to avoid copy)
    if [[ "$LOCAL_CACHE_DIR" != "$local_dest" && ! -e "$LOCAL_CACHE_DIR" ]]; then
        mkdir -p "$(dirname "$LOCAL_CACHE_DIR")"
        ln -sf "$local_dest" "$LOCAL_CACHE_DIR" 2>/dev/null || true
    fi

    # Restore pgdata permissions for HAF caches
    if [[ "$cache_type" == "haf" ]]; then
        _restore_pgdata_permissions "$local_dest"
    fi

    _update_lru "$cache_type" "$cache_key"
    return 0
}

# PUT: Copy local cache to NFS
cmd_put() {
    local cache_type="$1"
    local cache_key="$2"
    local local_source="$3"

    if [[ ! -d "$local_source" ]]; then
        _error "Source directory does not exist: $local_source"
        return 1
    fi

    # Relax pgdata permissions for HAF caches so they can be copied
    if [[ "$cache_type" == "haf" ]]; then
        _relax_pgdata_permissions "$local_source"
    fi

    _get_paths "$cache_type" "$cache_key"

    local is_nfs_host=false
    _is_nfs_host && is_nfs_host=true

    # On NFS host, LOCAL_CACHE_DIR == NFS_CACHE_DIR, so one copy does both
    if [[ "$is_nfs_host" == "true" ]]; then
        # Check if already exists
        if [[ -d "$NFS_CACHE_DIR" && -f "$METADATA_FILE" ]]; then
            _log "Cache already exists on NFS host, updating timestamp"
            _update_lru "$cache_type" "$cache_key"
            return 0
        fi

        # Copy directly to NFS path (which is local storage on this host)
        # Use tar streaming for consistency (though local-to-local is already fast)
        if [[ "$local_source" != "$NFS_CACHE_DIR" ]]; then
            _log "Storing cache on NFS host: $NFS_CACHE_DIR"
            mkdir -p "$NFS_CACHE_DIR"
            touch "$LOCK_FILE"
            _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$LOCK_FILE" -c "
                (cd '$local_source' && tar cf - .) | (cd '$NFS_CACHE_DIR' && tar xf -)
            " || { _error "Failed to store cache"; return 1; }
        else
            _log "Source is already at NFS path, no copy needed"
            mkdir -p "$(dirname "$METADATA_FILE")"
        fi

        _write_metadata "$cache_type" "$cache_key" "$NFS_CACHE_DIR"
        _update_lru "$cache_type" "$cache_key"
        _log "Cache stored successfully on NFS host"
        _maybe_cleanup &
        return 0
    fi

    # NFS client path: prefer NFS, use local cache only as fallback
    # Rationale: Local cache is only useful on THIS builder. NFS is shared across all builders.
    # We skip local copy to save time - if NFS push succeeds, create symlink for local reference.

    # Check if source is already on NFS - no need to copy/tar
    if [[ "$local_source" == "$CACHE_NFS_PATH"/* ]]; then
        _log "Source is already on NFS: $local_source"
        # Create symlink from expected cache path to actual location if different
        if [[ "$local_source" != "$NFS_CACHE_DIR" && ! -e "$NFS_CACHE_DIR" ]]; then
            ln -sf "$local_source" "$NFS_CACHE_DIR" 2>/dev/null || true
        fi
        _write_metadata "$cache_type" "$cache_key" "$local_source"
        _update_lru "$cache_type" "$cache_key"
        _log "Cache registered (source already on NFS)"
        return 0
    fi

    if ! _nfs_available; then
        # NFS unavailable - use local cache as fallback
        if [[ "$LOCAL_CACHE_DIR" != "$local_source" ]]; then
            _log "NFS not available, caching locally: $LOCAL_CACHE_DIR"
            mkdir -p "$(dirname "$LOCAL_CACHE_DIR")"
            cp -a "$local_source" "$LOCAL_CACHE_DIR" 2>/dev/null || true
        fi
        _log "Cached locally only (NFS unavailable)"
        return 0
    fi

    # Check if already exists on NFS (either as directory or tar archive)
    local NFS_TAR_FILE="${NFS_CACHE_DIR}.tar"
    if [[ -f "$NFS_TAR_FILE" ]] || { [[ -d "$NFS_CACHE_DIR" ]] && [[ -f "$METADATA_FILE" ]]; }; then
        _log "Cache already exists on NFS, updating timestamp"
        _update_lru "$cache_type" "$cache_key"
        return 0
    fi

    # Copy to NFS as single tar archive for 3x faster writes
    # Benchmark: cp -a 19GB/1844 files = 74s, tar archive = 25s
    # Writing single large file to NFS is much faster than many small files
    mkdir -p "$(dirname "$NFS_TAR_FILE")"
    local NFS_TAR_LOCK="${NFS_TAR_FILE}.lock"
    touch "$NFS_TAR_LOCK"

    # Build exclusions for caches to reduce size and speed up NFS writes
    # - hive caches: exclude blockchain (~1.7GB) - services use /blockchain/block_log_5m (local mount)
    # - HAF caches: exclude blockchain (~1.7GB) - WAL files are kept for safe recovery
    local tar_excludes=""
    if [[ "$cache_type" == "hive" ]]; then
        # Exclude blockchain - CI runners mount /blockchain locally via services_volumes
        if [[ -d "${local_source}/datadir/blockchain" ]]; then
            tar_excludes="--exclude=./datadir/blockchain"
            _log "Excluding datadir/blockchain (services use local /blockchain/block_log_5m)"
        fi
    elif [[ "$cache_type" == "haf" || "$cache_type" == "haf_sync" ]]; then
        tar_excludes=$(_build_haf_tar_excludes "$local_source")
    fi

    # Write exclusions to temp file for use in subshell
    local excludes_file=""
    if [[ -n "$tar_excludes" ]]; then
        excludes_file=$(mktemp)
        echo "$tar_excludes" > "$excludes_file"
    fi

    # Check for stale locks before attempting to acquire
    _check_stale_lock "$NFS_TAR_LOCK"

    local lock_start_time=$(date +%s.%N)
    _log "Attempting to acquire lock: $NFS_TAR_LOCK"

    if ! _flock_with_timeout "$CACHE_LOCK_TIMEOUT" -x "$NFS_TAR_LOCK" -c "
        # Record lock acquisition time
        lock_acquired=\$(date +%s.%N)
        echo \"[cache-manager] Lock acquired in \$(echo \"\$lock_acquired - $lock_start_time\" | bc)s\" >&2

        # Write lock holder info for debugging
        cat > '${NFS_TAR_LOCK}.info' 2>/dev/null <<LOCKINFO || true
hostname=\$(hostname)
pid=\$\$
started=\$(date -Iseconds)
job_id=${CI_JOB_ID:-unknown}
pipeline_id=${CI_PIPELINE_ID:-unknown}
LOCKINFO

        # Double-check after acquiring lock
        if [[ -f '$NFS_TAR_FILE' ]]; then
            echo '[cache-manager] Cache was created while waiting for lock' >&2
            exit 0
        fi

        tar_start=\$(date +%s.%N)
        echo '[cache-manager] Creating tar archive on NFS: $NFS_TAR_FILE' >&2
        # Read exclusions from temp file if present
        excludes=''
        if [[ -f '$excludes_file' ]]; then
            excludes=\$(cat '$excludes_file')
        fi
        # Write tar archive directly to NFS (single file = fast)
        # shellcheck disable=SC2086
        tar cf '$NFS_TAR_FILE.tmp' \$excludes -C '$local_source' .
        tar_end=\$(date +%s.%N)
        tar_duration=\$(echo \"\$tar_end - \$tar_start\" | bc)

        # Get file size for throughput calculation
        tar_size=\$(stat -c %s '$NFS_TAR_FILE.tmp' 2>/dev/null || echo 0)
        tar_size_gb=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / 1024\" | bc)
        throughput=\$(echo \"scale=2; \$tar_size / 1024 / 1024 / \$tar_duration\" | bc 2>/dev/null || echo '?')

        echo \"[cache-manager] Tar completed: \${tar_size_gb}GB in \${tar_duration}s (\${throughput} MB/s)\" >&2

        mv '$NFS_TAR_FILE.tmp' '$NFS_TAR_FILE'

        # Clean up lock info file
        rm -f '${NFS_TAR_LOCK}.info' 2>/dev/null || true

        total_duration=\$(echo \"\$(date +%s.%N) - $lock_start_time\" | bc)
        echo \"[cache-manager] Total put operation: \${total_duration}s\" >&2
    "; then
        [[ -n "$excludes_file" ]] && rm -f "$excludes_file"
        _error "Failed to acquire exclusive lock"
        return 1
    fi
    [[ -n "$excludes_file" ]] && rm -f "$excludes_file"

    # Write metadata next to tar file
    local TAR_METADATA="${NFS_TAR_FILE%.tar}/.metadata"
    mkdir -p "$(dirname "$TAR_METADATA")"
    _write_metadata "$cache_type" "$cache_key" "$local_source"
    mv "$METADATA_FILE" "$TAR_METADATA" 2>/dev/null || true

    # Create local symlink to source for future local hits (instant, no copy)
    if [[ "$LOCAL_CACHE_DIR" != "$local_source" && ! -e "$LOCAL_CACHE_DIR" ]]; then
        mkdir -p "$(dirname "$LOCAL_CACHE_DIR")"
        ln -sf "$local_source" "$LOCAL_CACHE_DIR" 2>/dev/null || true
        _log "Created local cache symlink: $LOCAL_CACHE_DIR -> $local_source"
    fi

    _update_lru "$cache_type" "$cache_key"
    _log "Cache stored successfully (tar archive)"

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
    local search_path="$CACHE_NFS_PATH"
    [[ -n "$cache_type" ]] && search_path="$CACHE_NFS_PATH/$cache_type"

    local total_size
    total_size=$(du -sb "$search_path" 2>/dev/null | awk '{print $1}' | head -1) || total_size=0
    [[ -z "$total_size" || ! "$total_size" =~ ^[0-9]+$ ]] && total_size=0
    _log "Current cache size: $((total_size / 1024 / 1024 / 1024))GB"

    if [[ ! -f "$lru_index" ]]; then
        _log "No LRU index found, nothing to clean"
        return 0
    fi

    # Sort by timestamp (oldest first) and process
    local removed=0
    while IFS='|' read -r timestamp entry; do
        # Skip if filtering by type and doesn't match
        if [[ -n "$cache_type" && ! "$entry" =~ ^${cache_type}/ ]]; then
            continue
        fi

        local entry_path="$CACHE_NFS_PATH/$entry"

        # Skip if doesn't exist
        [[ -d "$entry_path" ]] || continue

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
            local lock_file="$entry_path/.lock"
            if [[ -f "$lock_file" ]] && ! flock -n "$lock_file" -c "true" 2>/dev/null; then
                _log "Skipping $entry - currently locked"
                continue
            fi

            local entry_size=$(du -sb "$entry_path" 2>/dev/null | cut -f1 || echo 0)
            _log "Removing: $entry (${entry_size} bytes)"
            rm -rf "$entry_path"
            total_size=$((total_size - entry_size))
            removed=$((removed + 1))

            # Remove from LRU index
            grep -v "|${entry}$" "$lru_index" > "${lru_index}.tmp" 2>/dev/null || true
            mv "${lru_index}.tmp" "$lru_index"
        fi

        # Stop if under limit
        [[ $total_size -le $max_size_bytes ]] && break

    done < <(sort -t'|' -k1 -n "$lru_index")

    _log "Cleanup complete, removed $removed entries"
}

# Maybe trigger cleanup if size is getting large
_maybe_cleanup() {
    if ! _nfs_available; then
        return 0
    fi

    local total_size
    total_size=$(du -sb "$CACHE_NFS_PATH" 2>/dev/null | awk '{print $1}') || total_size=0
    [[ -z "$total_size" ]] && total_size=0
    local max_bytes=$((CACHE_MAX_SIZE_GB * 1024 * 1024 * 1024))
    local threshold=$((max_bytes * 90 / 100))  # 90% threshold

    if [[ "$total_size" =~ ^[0-9]+$ ]] && [[ $total_size -gt $threshold ]]; then
        _log "Cache size at 90% capacity, triggering cleanup"
        cmd_cleanup "" --max-size-gb "$CACHE_MAX_SIZE_GB" --max-age-days "$CACHE_MAX_AGE_DAYS"
    fi
}

# LIST: Show caches of a given type
cmd_list() {
    local cache_type="${1:-}"

    echo "=== Local Caches (${CACHE_LOCAL_PATH}) ==="
    local pattern="${CACHE_LOCAL_PATH}/${cache_type}*"
    for dir in $pattern; do
        [[ -d "$dir" ]] || continue
        local size=$(du -sh "$dir" 2>/dev/null | cut -f1 || echo "?")
        local mtime=$(stat -c %y "$dir" 2>/dev/null | cut -d. -f1 || echo "?")
        echo "  $(basename "$dir") - ${size} - ${mtime}"
    done

    if _nfs_available; then
        echo ""
        echo "=== NFS Caches (${CACHE_NFS_PATH}) ==="
        local nfs_path="$CACHE_NFS_PATH"
        [[ -n "$cache_type" ]] && nfs_path="$CACHE_NFS_PATH/$cache_type"

        if [[ -d "$nfs_path" ]]; then
            for dir in "$nfs_path"/*/; do
                [[ -d "$dir" ]] || continue
                local size=$(du -sh "$dir" 2>/dev/null | cut -f1 || echo "?")
                local key=$(basename "$dir")
                local meta=""
                if [[ -f "$dir/.metadata" ]]; then
                    meta=$(jq -r '.created_at // "?"' "$dir/.metadata" 2>/dev/null || echo "?")
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
    echo "NFS Path:     $CACHE_NFS_PATH"
    echo "Local Path:   $CACHE_LOCAL_PATH"
    echo "Max Size:     ${CACHE_MAX_SIZE_GB}GB"
    echo "Max Age:      ${CACHE_MAX_AGE_DAYS} days"
    echo "NFS Host:     $(_is_nfs_host && echo "YES (local storage)" || echo "NO (NFS client)")"
    echo ""

    local lru_index="${CACHE_NFS_PATH}/.lru_index"

    if _nfs_available; then
        echo "NFS Status:   AVAILABLE"
        local total=$(du -sh "$CACHE_NFS_PATH" 2>/dev/null | cut -f1 || echo "?")
        echo "NFS Usage:    $total"

        if [[ -f "$lru_index" ]]; then
            local count=$(wc -l < "$lru_index")
            echo "Cache Entries: $count"
        fi
    else
        echo "NFS Status:   NOT AVAILABLE"
    fi

    echo ""
    echo "Local Usage:"
    du -sh "${CACHE_LOCAL_PATH}"/* 2>/dev/null | head -10 || echo "  (empty)"
}

# Main
usage() {
    head -20 "$0" | tail -18 | sed 's/^# //'
    exit 1
}

[[ $# -lt 1 ]] && usage

cmd="$1"
shift

case "$cmd" in
    get)
        [[ $# -lt 3 ]] && { _error "get requires: <cache-type> <cache-key> <local-dest>"; exit 1; }
        cmd_get "$@"
        ;;
    put)
        [[ $# -lt 3 ]] && { _error "put requires: <cache-type> <cache-key> <local-source>"; exit 1; }
        cmd_put "$@"
        ;;
    cleanup)
        cmd_cleanup "$@"
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
