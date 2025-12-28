# cache-manager.sh

Centralized CI cache manager with NFS backing for cross-builder cache sharing.

## Overview

The cache-manager provides a shared caching layer for CI jobs across multiple build servers. It uses NFS as the shared storage backend with local caching for performance, and implements LRU eviction for space management.

**Primary use cases:**
- HAF replay data caching (PostgreSQL data directories)
- Hive replay data caching
- Downstream project caches (hivemind, balance_tracker, etc.)

**Storage format:** All caches are stored as tar archives (`<key>.tar`) for consistent behavior and optimal NFS performance.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         NFS Server                               │
│                    (hive-builder-10)                             │
│  /storage1/ci-cache/  ←──symlink── /nfs/ci-cache/               │
│     ├── hive/                                                    │
│     │   └── <commit-sha>.tar                                    │
│     ├── haf/                                                     │
│     │   └── <commit-sha>.tar                                    │
│     ├── .lru_index                                               │
│     └── .global_lock                                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                         NFS mount
                              │
┌─────────────────────────────┼─────────────────────────────────┐
│  hive-builder-8             │           hive-builder-9        │
│  /nfs/ci-cache/ (mount)     │    /nfs/ci-cache/ (mount)       │
│  /cache/ (local SSD)        │    /cache/ (local SSD)          │
│     └── hive_<sha>.tar      │       └── haf_<sha>.tar         │
└─────────────────────────────┴─────────────────────────────────┘
```

### Immutable Local Cache

Local caches are stored as tar files, not directories. This ensures:
- **Immutability**: Jobs cannot accidentally corrupt the cache by modifying extracted files
- **Clean extraction**: Each job gets a pristine copy of the data
- **Predictable behavior**: No risk of state leaking between jobs

### NFS Host vs NFS Client

The script automatically detects whether it's running on the NFS server or a client:

- **NFS Host** (hive-builder-10): `/nfs/ci-cache` is a symlink to local storage. No network I/O for cache operations.
- **NFS Clients**: `/nfs/ci-cache` is an NFS mount point. Cache reads/writes go over the network.

Both NFS host and clients use the same tar archive format for consistency. On the NFS host, tar operations are local I/O (fast), while on clients they go over the network.

## Commands

### get

```bash
cache-manager.sh get <cache-type> <cache-key> <local-dest>
```

Retrieves a cache entry. Search order:
1. Local tar cache (`/cache/<type>_<key>.tar`)
2. NFS tar archive (`/nfs/ci-cache/<type>/<key>.tar`)

**Always extracts fresh**: Even on local cache hit, the tar is extracted to `<local-dest>`. This ensures jobs get pristine data and cannot corrupt the cache.

On NFS cache hit, the tar is also copied to local cache for future use on the same builder.

**Locking:** Uses shared lock (`flock -s`) during extraction so multiple jobs can read simultaneously.

### put

```bash
cache-manager.sh put <cache-type> <cache-key> <local-source>
```

Stores a cache entry to NFS as a tar archive.

**Locking:** Uses exclusive lock (`flock -x`) to prevent concurrent writes.

**Behavior:**
1. Creates local tar first (always succeeds if disk space available)
2. Pushes local tar to NFS (if available)
3. If NFS unavailable or push fails, local cache still exists

This "local-first" approach ensures the builder always has a local cache after PUT, avoiding NFS fetch if the next job lands on the same builder.

**Optimizations:**
- Skips if cache already exists on NFS (but ensures local copy exists)
- Uses atomic rename (`.tmp` → `.tar`) to prevent partial writes
- Excludes `datadir/blockchain` from hive/haf caches (jobs use local block_log mount)

### cleanup

```bash
cache-manager.sh cleanup <cache-type> [--max-size-gb N] [--max-age-days N]
```

Removes old caches using LRU eviction. Triggered automatically when cache reaches 90% capacity.

### list / status

```bash
cache-manager.sh list [cache-type]
cache-manager.sh status
```

Display cache contents and overall status.

### is-fast-builder

```bash
cache-manager.sh is-fast-builder
```

Returns 0 if running on a fast builder (AMD 5950/5900/EPYC). Used for job scheduling decisions.

## Locking Mechanism

### Lock Types

| Lock | File | Mode | Purpose |
|------|------|------|---------|
| Tar lock | `<cache>.tar.lock` | Exclusive (`-x`) for PUT, Shared (`-s`) for GET | Serialize tar archive access |
| Global lock | `.global_lock` | Exclusive | LRU index updates |

### Lock Holder Info

When acquiring a lock, the script writes debug info to `<lockfile>.info`:

```
hostname=hive-builder-8
pid=12345
started=2025-01-15T10:30:00+00:00
job_id=1234567
pipeline_id=98765
```

This helps diagnose stale locks.

### flock Requirements

**util-linux is required.** BusyBox flock does not work with NFS - it returns "Bad file descriptor" when locking NFS files.

The script checks for proper flock support at startup and fails with a clear error if BusyBox flock is detected:

```
[cache-manager] ERROR: BusyBox flock detected - this does not work with NFS!
[cache-manager] ERROR: Install util-linux package: apk add util-linux (Alpine)
```

The `docker-builder` and `docker-dind` images already include util-linux.

## Handling Failures and Canceled Jobs

### Stale Lock Detection

Jobs can be canceled mid-operation, leaving lock files behind. The script handles this:

1. **Age check:** Lock files older than `CACHE_STALE_LOCK_MINUTES` (default: 10) are considered potentially stale

2. **Active lock check:** Tests if the lock is actually held:
   ```bash
   flock -n "$lockfile" -c "true"  # Returns 0 if NOT held
   ```

3. **Stale lock cleanup:**
   - If lock file is old AND not actively held: silently remove it
   - If lock file is old AND still held: log warning with holder info, then break it

```bash
_check_stale_lock() {
    # Lock older than threshold?
    if [[ $lock_age_minutes -lt $stale_minutes ]]; then
        return 1  # Not stale
    fi

    # Lock actually held?
    if flock -n "$lockfile" -c "true"; then
        rm -f "$lockfile"  # Just a leftover file
        return 0
    fi

    # Held but ancient - break it
    _log "Breaking stale lock (${lock_age_minutes} min old)"
    rm -f "$lockfile" "${lockfile}.info"
    return 0
}
```

### Incomplete Cache Entries

**PUT operations use atomic rename:**

```bash
tar cf '$NFS_TAR_FILE.tmp' -C '$local_source' .
mv '$NFS_TAR_FILE.tmp' '$NFS_TAR_FILE'  # Atomic on POSIX
```

If a job is canceled during tar creation:
- The `.tmp` file remains (incomplete)
- The final `.tar` file doesn't exist
- Next job sees cache miss and creates a fresh cache
- Old `.tmp` files can be cleaned up manually or by periodic maintenance

**Double-check after lock acquisition:**

```bash
# Inside locked section
if [[ -f '$NFS_TAR_FILE' ]]; then
    echo 'Cache was created while waiting for lock'
    exit 0
fi
```

This prevents duplicate work when multiple jobs race to create the same cache.

### Cleanup During Operations

The `cleanup` command skips entries that are currently locked:

```bash
if ! flock -n "$lock_file" -c "true"; then
    _log "Skipping $entry - currently locked"
    continue
fi
```

## PostgreSQL Data Handling

HAF caches contain PostgreSQL data directories which require special handling:

### Permission Management

PostgreSQL requires `pgdata` to be mode 700, owned by the postgres user (uid 105).

**Before caching (relax permissions):**
```bash
sudo chmod -R a+rX "$pgdata_path"  # Make readable for tar
```

**After extraction (restore permissions):**
```bash
sudo chmod 700 "$pgdata_path"
sudo chown -R 105:105 "$pgdata_path"
```

### Tablespace Symlink Handling

PostgreSQL creates absolute symlinks in `pg_tblspc/`:
```
pg_tblspc/16396 -> /home/hived/datadir/haf_db_store/tablespace
```

These break when data is extracted to a different location. The script converts them to relative paths:
```
pg_tblspc/16396 -> ../../tablespace
```

### WAL File Preservation

All WAL files are kept in the cache. Previously there was an attempt to exclude WAL files to save ~5.8GB, but this caused data corruption during PostgreSQL crash recovery.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_NFS_PATH` | `/nfs/ci-cache` | NFS mount point |
| `CACHE_LOCAL_PATH` | `/cache` | Local cache directory |
| `CACHE_MAX_SIZE_GB` | `2000` | Max total NFS cache size |
| `CACHE_MAX_AGE_DAYS` | `30` | Max cache age for eviction |
| `CACHE_LOCK_TIMEOUT` | `120` | Lock timeout in seconds |
| `CACHE_STALE_LOCK_MINUTES` | `10` | Break locks older than this |
| `CACHE_QUIET` | `false` | Suppress verbose output |

## CI Integration

### Required Tags

Jobs using the cache-manager should use:
```yaml
tags:
  - data-cache-storage  # Has /nfs/ci-cache mounted
  - fast                # AMD 5950 builders (faster replays)
```

### Example Usage

```yaml
prepare_haf_data:
  image: registry.gitlab.syncad.com/hive/common-ci-configuration/docker-builder
  script:
    - |
      # Try to get from cache
      if cache-manager.sh get haf "$HAF_COMMIT" /data; then
        echo "Cache hit!"
      else
        echo "Cache miss, running replay..."
        ./run-replay.sh /data
        cache-manager.sh put haf "$HAF_COMMIT" /data
      fi
```

## Troubleshooting

### Stuck Locks

Check lock holder info:
```bash
cat /nfs/ci-cache/haf/<key>.tar.lock.info
```

Manually break a lock:
```bash
rm -f /nfs/ci-cache/haf/<key>.tar.lock{,.info}
```

### NFS Performance

Tar archives are used instead of directories because writing a single large file to NFS is ~3x faster than many small files:
- `cp -a` 19GB/1844 files: 74s
- `tar` single archive: 25s

### Cache Miss When Expected Hit

1. Check if tar exists: `ls -la /nfs/ci-cache/<type>/<key>.tar`
2. Check lock status: `flock -n <lockfile> -c "echo unlocked" || echo "locked"`
3. Check NFS mount: `mountpoint /nfs/ci-cache`

### Incomplete Caches

Look for `.tmp` files:
```bash
find /nfs/ci-cache -name "*.tmp" -mmin +60
```

These are from interrupted PUT operations and can be safely removed.

### Local Cache Management

Local caches are stored as tar files in `/cache/`:
```bash
# List local caches
ls -la /cache/*.tar

# Remove stale local caches (older than 7 days)
find /cache -name "*.tar" -mtime +7 -delete
```

Local caches are automatically populated when fetching from NFS and persist across jobs on the same builder.
