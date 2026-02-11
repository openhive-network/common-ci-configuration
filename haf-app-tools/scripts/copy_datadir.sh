#!/bin/bash
#
# Copies HAF data directory with NFS cache fallback
# Fetched from: common-ci-configuration/haf-app-tools/scripts/copy_datadir.sh
#

set -xeuo pipefail

# Default shared block_log location (used when blockchain not in cache)
# Use local static copy on each builder (faster than NFS)
SHARED_BLOCK_LOG_DIR="${SHARED_BLOCK_LOG_DIR:-/blockchain/block_log_5m}"

# NFS cache configuration
CACHE_NFS_PATH="${CACHE_NFS_PATH:-/nfs/ci-cache}"

# Cache manager script - fetch from common-ci-configuration if not available locally
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_CI_URL="${COMMON_CI_URL:-https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/develop}"

# Try local paths first, then fetch
CACHE_MANAGER=""
for path in "$SCRIPT_DIR/cache-manager.sh" "$SCRIPT_DIR/../cache-manager.sh" "/tmp/cache-manager.sh"; do
    if [[ -x "$path" ]]; then
        CACHE_MANAGER="$path"
        break
    fi
done

if [[ -z "$CACHE_MANAGER" ]]; then
    CACHE_MANAGER="/tmp/cache-manager.sh"
    # Use wget (available in HAF containers) with curl as fallback
    if command -v wget &>/dev/null; then
        wget -q "${COMMON_CI_URL}/scripts/cache-manager.sh" -O "$CACHE_MANAGER" 2>/dev/null || true
    elif command -v curl &>/dev/null; then
        curl -fsSL "${COMMON_CI_URL}/scripts/cache-manager.sh" -o "$CACHE_MANAGER" 2>/dev/null || true
    fi
    chmod +x "$CACHE_MANAGER" 2>/dev/null || true
fi

# Completion marker file name (must match cache-manager.sh)
CACHE_COMPLETION_MARKER=".extraction_complete"

# Validate that a HAF cache directory is complete (not just existing)
# Returns 0 if valid, 1 if invalid/incomplete
# This prevents using corrupted caches when the directory exists but files are missing
validate_cache_integrity() {
    local data_source="$1"
    local datadir="${data_source}/datadir"

    # Basic check: datadir must exist
    if [[ ! -d "$datadir" ]]; then
        echo "Cache validation failed: $datadir does not exist"
        return 1
    fi

    # Check for completion marker (written by cache-manager after successful extraction)
    if [[ ! -f "${data_source}/${CACHE_COMPLETION_MARKER}" ]]; then
        echo "Cache validation failed: completion marker missing (extraction was interrupted)"
        return 1
    fi

    # For HAF caches: validate pgdata structure exists
    local pgdata="${datadir}/haf_db_store/pgdata"
    local tablespace="${datadir}/haf_db_store/tablespace"

    # If this looks like a HAF cache (has haf_db_store), validate it properly
    if [[ -d "${datadir}/haf_db_store" ]]; then
        # pgdata must exist
        if [[ ! -d "$pgdata" ]]; then
            echo "Cache validation failed: pgdata directory missing at $pgdata"
            return 1
        fi

        # tablespace must exist (PostgreSQL won't start without it)
        if [[ ! -d "$tablespace" ]]; then
            echo "Cache validation failed: tablespace directory missing at $tablespace"
            return 1
        fi

        # Check for critical pgdata subdirectories (PostgreSQL requires these)
        local required_dirs=("pg_wal" "pg_tblspc" "base" "global")
        for dir in "${required_dirs[@]}"; do
            if ! sudo test -d "${pgdata}/${dir}" 2>/dev/null; then
                echo "Cache validation failed: required pgdata directory missing: ${dir}"
                return 1
            fi
        done

        # Check for PG_VERSION file (basic PostgreSQL sanity check)
        if ! sudo test -f "${pgdata}/PG_VERSION" 2>/dev/null; then
            echo "Cache validation failed: PG_VERSION file missing in pgdata"
            return 1
        fi

        # Sanity check: tablespace should have a minimum number of files
        # A partially-extracted cache can pass directory checks but have missing data files
        local file_count
        file_count=$(sudo find "$tablespace" -type f 2>/dev/null | wc -l)
        if [[ "$file_count" -lt 50 ]]; then
            echo "Cache validation failed: tablespace has only $file_count files (expected 50+)"
            return 1
        fi

        echo "Cache validation passed: pgdata structure is complete ($file_count tablespace files)"
    fi

    return 0
}

# Fix pg_tblspc symlinks to point to the correct tablespace location
# PostgreSQL stores tablespace symlinks with absolute paths, which break when data is copied
# Uses relative symlinks so they work both on the host AND inside Docker containers
fix_pg_tblspc_symlinks() {
    local datadir="$1"
    local pg_tblspc="${datadir}/haf_db_store/pgdata/pg_tblspc"
    local tablespace="${datadir}/haf_db_store/tablespace"

    if [[ ! -d "$pg_tblspc" ]]; then
        return 0
    fi

    for link in "$pg_tblspc"/*; do
        if [[ -L "$link" ]]; then
            local target
            target=$(readlink "$link")
            # Fix if symlink contains 'tablespace' (relative or wrong absolute path)
            # Use relative path so it works inside Docker containers too
            if [[ "$target" == *"tablespace"* ]] && [[ -d "$tablespace" ]]; then
                # Relative path from pg_tblspc/16396 to tablespace is ../../tablespace
                local relative_path="../../tablespace"
                echo "Fixing pg_tblspc symlink: $(basename "$link") -> $relative_path"
                sudo rm -f "$link"
                sudo ln -s "$relative_path" "$link"
            fi
        fi
    done
}

# Function to extract NFS cache if local DATA_SOURCE doesn't exist
# Delegates to cache-manager.sh for unified cache handling
# Derives cache type and key from DATA_SOURCE path pattern: /cache/{type}_{key}
extract_nfs_cache_if_needed() {
    local data_source="$1"

    # Validate cache integrity, not just directory existence
    # This catches corrupted caches where directory exists but files are missing
    if validate_cache_integrity "$data_source"; then
        echo "Local cache exists and is valid at ${data_source}/datadir"
        return 0
    fi

    # Cache is missing or incomplete - clean up any stale data before extracting
    if [[ -d "${data_source}" ]]; then
        echo "Removing incomplete/corrupted cache at ${data_source}"
        sudo rm -rf "${data_source}" 2>/dev/null || rm -rf "${data_source}" 2>/dev/null || true
    fi

    # Parse DATA_SOURCE to derive cache type and key
    # Pattern: /cache/{type}_{key} -> cache-manager get {type} {key} {data_source}
    local basename
    basename=$(basename "$data_source")

    # Split by pattern to extract cache type and key
    # Special case: replay_data_hive_{commit} -> type=hive, key={commit}
    # Common patterns: haf_{commit} -> type=haf, key={commit}
    #                  hive_{commit} -> type=hive, key={commit}
    local cache_type cache_key
    if [[ "$basename" =~ ^replay_data_hive_(.+)$ ]]; then
        # Special case for HIVE replay data: map to hive/{commit}.tar
        cache_type="hive"
        cache_key="${BASH_REMATCH[1]}"
    elif [[ "$basename" =~ ^([^_]+_[^_]+)_(.+)$ ]]; then
        cache_type="${BASH_REMATCH[1]}"
        cache_key="${BASH_REMATCH[2]}"
    elif [[ "$basename" =~ ^([^_]+)_(.+)$ ]]; then
        cache_type="${BASH_REMATCH[1]}"
        cache_key="${BASH_REMATCH[2]}"
    else
        echo "Cannot parse DATA_SOURCE path for NFS fallback: $data_source"
        return 1
    fi

    echo "Attempting NFS cache retrieval: type=$cache_type key=$cache_key"

    # Use cache-manager if available, otherwise fall back to direct NFS access
    if [[ -x "$CACHE_MANAGER" ]]; then
        echo "Using cache-manager for NFS fallback"
        if "$CACHE_MANAGER" get "$cache_type" "$cache_key" "$data_source"; then
            echo "Cache-manager retrieved cache successfully"
            # Note: cache-manager handles pg_tblspc symlinks and pgdata permissions
            # inside its exclusive lock to prevent race conditions
            return 0
        else
            echo "Cache-manager could not retrieve cache"
            return 1
        fi
    else
        # Fallback: direct tar extraction (for environments without cache-manager)
        # Check local tar first (fast), then NFS tar (slow)
        local local_tar="${data_source}.tar"
        local nfs_tar="${CACHE_NFS_PATH}/${cache_type}/${cache_key}.tar"
        local tar_file=""

        echo "Cache-manager not found, checking for tar files..."

        if [[ -f "$local_tar" ]]; then
            echo "Found local cache tar: $local_tar"
            tar_file="$local_tar"
        elif [[ -f "$nfs_tar" ]]; then
            echo "Local tar not found, using NFS tar: $nfs_tar"
            tar_file="$nfs_tar"
        fi

        if [[ -n "$tar_file" ]]; then
            echo "Extracting $tar_file to $data_source"
            mkdir -p "$data_source"
            chmod 777 "$data_source" 2>/dev/null || true

            # Use flock to prevent race conditions when multiple jobs extract to the same cache dir
            # All post-extraction fixes (permissions, symlinks) must be inside the lock to prevent
            # race conditions with concurrent readers. See: HAfAH pipeline 150169 failure.
            if flock "$data_source" bash -c "
                if [[ -d \"${data_source}/datadir\" ]]; then
                    echo 'Cache already extracted by another job'
                    exit 0
                fi
                tar xf \"$tar_file\" -C \"$data_source\"

                # Restore pgdata permissions for PostgreSQL (inside lock)
                pgdata=\"${data_source}/datadir/haf_db_store/pgdata\"
                tablespace=\"${data_source}/datadir/haf_db_store/tablespace\"
                pg_tblspc=\"${data_source}/datadir/haf_db_store/pgdata/pg_tblspc\"

                if [[ -d \"\$pgdata\" ]]; then
                    chmod 700 \"\$pgdata\" 2>/dev/null || true
                    chown -R 105:105 \"\$pgdata\" 2>/dev/null || true
                fi
                if [[ -d \"\$tablespace\" ]]; then
                    chmod 700 \"\$tablespace\" 2>/dev/null || true
                    chown -R 105:105 \"\$tablespace\" 2>/dev/null || true
                fi

                # Fix pg_tblspc symlinks - only if absolute paths (inside lock)
                if [[ -d \"\$pg_tblspc\" ]]; then
                    for link in \"\$pg_tblspc\"/*; do
                        if [[ -L \"\$link\" ]]; then
                            target=\$(readlink \"\$link\")
                            if [[ \"\$target\" == /* ]] && [[ \"\$target\" == *tablespace* ]]; then
                                echo \"Fixing pg_tblspc symlink: \$(basename \"\$link\")\"
                                sudo rm -f \"\$link\" 2>/dev/null || rm -f \"\$link\"
                                sudo ln -s '../../tablespace' \"\$link\" 2>/dev/null || ln -s '../../tablespace' \"\$link\"
                            fi
                        fi
                    done
                fi
            "; then
                echo "Cache extracted successfully from $tar_file"
                return 0
            else
                echo "ERROR: Failed to extract cache from $tar_file"
                return 1
            fi
        else
            echo "No cache tar found (checked $local_tar and $nfs_tar)"
            return 1
        fi
    fi
}

if [ -n "${DATA_SOURCE+x}" ]
then
    echo "DATA_SOURCE: ${DATA_SOURCE}"
    echo "DATADIR: ${DATADIR}"

    # Validate cache integrity - if incomplete/corrupted, try NFS fallback
    # This catches cases where directory exists but files are missing
    if ! validate_cache_integrity "${DATA_SOURCE}"; then
        echo "Local DATA_SOURCE missing or incomplete, attempting NFS fallback..."
        if ! extract_nfs_cache_if_needed "${DATA_SOURCE}"; then
            echo "ERROR: Failed to retrieve cache and no valid local data exists"
            exit 1
        fi
    fi

    # Final validation: ensure datadir is complete after all extraction attempts
    if ! validate_cache_integrity "${DATA_SOURCE}"; then
        echo "ERROR: DATA_SOURCE/datadir is incomplete or corrupted after extraction attempts"
        exit 1
    fi

    if [ "$(realpath "${DATA_SOURCE}/datadir")" != "$(realpath "${DATADIR}")" ]
    then
        echo "Creating copy of ${DATA_SOURCE}/datadir inside ${DATADIR}"
        sudo -Enu hived mkdir -p "${DATADIR}"
        # Use cp without -p to avoid "Operation not supported" errors when copying from NFS
        # Use shared lock (-s) since source data is immutable after extraction - allows parallel reads
        flock -s "${DATA_SOURCE}/datadir" sudo -En cp -r --no-preserve=mode,ownership "${DATA_SOURCE}/datadir"/*  "${DATADIR}"

        # Ensure all writes are flushed to disk before PostgreSQL starts
        # Prevents DataCorrupted errors from unflushed write buffers
        sync

        # Fix pg_tblspc symlinks after copying to DATADIR
        fix_pg_tblspc_symlinks "${DATADIR}"

        # Handle blockchain directory - may be excluded from cache for efficiency
        # Check if directory exists AND has block_log files (empty dirs can be created by Docker bind mounts)
        if [[ -d "${DATA_SOURCE}/datadir/blockchain" ]] && ls "${DATA_SOURCE}/datadir/blockchain"/block_log* 1>/dev/null 2>&1; then
            sudo chmod -R a+w "${DATA_SOURCE}/datadir/blockchain"
            ls -al "${DATA_SOURCE}/datadir/blockchain"
        elif [[ -d "${SHARED_BLOCK_LOG_DIR}" ]]; then
            # Blockchain not in cache or empty - create symlinks to shared block_log
            # Remove empty blockchain dir if it exists (leftover from Docker bind mounts)
            if [[ -d "${DATADIR}/blockchain" ]] && [[ -z "$(ls -A "${DATADIR}/blockchain" 2>/dev/null)" ]]; then
                rmdir "${DATADIR}/blockchain" 2>/dev/null || true
            fi
            echo "Blockchain not in cache, linking to shared block_log at ${SHARED_BLOCK_LOG_DIR}"
            sudo -Enu hived mkdir -p "${DATADIR}/blockchain"
            # Fix blockchain directory ownership if copied by root (needed for non-empty dirs like rocksdb)
            if [[ -d "${DATADIR}/blockchain" ]]; then
                sudo chown -R hived:users "${DATADIR}/blockchain" 2>/dev/null || sudo chmod -R a+w "${DATADIR}/blockchain" 2>/dev/null || true
            fi
            for block_file in "${SHARED_BLOCK_LOG_DIR}"/block_log* ; do
                if [[ -f "$block_file" ]]; then
                    local_name=$(basename "$block_file")
                    sudo -Enu hived ln -sf "$block_file" "${DATADIR}/blockchain/${local_name}"
                    echo "Linked: ${local_name}"
                fi
            done
            ls -al "${DATADIR}/blockchain"
        else
            echo "WARNING: No blockchain in cache and shared block_log not found at ${SHARED_BLOCK_LOG_DIR}"
        fi

        if [[ -e "${DATA_SOURCE}/shm_dir" && "$(realpath "${DATA_SOURCE}/shm_dir")" != "$(realpath "${SHM_DIR}")" ]]
        then
            echo "Creating copy of ${DATA_SOURCE}/shm_dir inside ${SHM_DIR}"
            sudo -Enu hived mkdir -p "${SHM_DIR}"
            # Use cp without -p to avoid "Operation not supported" errors when copying from NFS
            # Use shared lock (-s) since source data is immutable after extraction - allows parallel reads
            flock -s "${DATA_SOURCE}/datadir" sudo -En cp -r --no-preserve=mode,ownership "${DATA_SOURCE}/shm_dir"/* "${SHM_DIR}"
            sync
            sudo chmod -R a+w "${SHM_DIR}"
            ls -al "${SHM_DIR}"
        else
            echo "Skipping shm_dir processing."
        fi
        ls -al "${DATA_SOURCE}/datadir"
    fi
fi
