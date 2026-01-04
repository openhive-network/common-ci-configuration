# CI Consolidation Roadmap

This document outlines the plan for consolidating common CI patterns into `common-ci-configuration` to reduce duplication across Hive projects.

## Current State

### Include Hierarchy Problem

Currently, CI templates have a 4-level deep include chain:

```
HAF apps (balance_tracker, reputation_tracker)
    ↓ includes (pinned HAF commit)
HAF/scripts/ci-helpers/prepare_data_image_job.yml
    ↓ includes (pinned hive commit)
hive/scripts/ci-helpers/prepare_data_image_job.yml
    ↓ includes (pinned common-ci-configuration commit)
common-ci-configuration/templates/*.yml
```

Each layer must track specific commit SHAs of the layer above:
- balance_tracker pins HAF: `ref: 1ac5d12...`
- HAF pins hive: `ref: 8d24f31...`
- hive pins common-ci-configuration: `ref: 7cc2791...`

**Problems:**
- Bug fixes require cascading MRs through each layer
- Version drift between projects
- Complex dependency tracking
- Slow propagation of improvements

### Already in common-ci-configuration

| Script/Template | Description | Used By |
|-----------------|-------------|---------|
| `scripts/cache-manager.sh` | NFS cache get/put operations | HAF, hive, HAF apps |
| `scripts/prepare_data_and_shm_dir.sh` | Prepare datadir with block_log | HAF, hive |
| `scripts/build_data.sh` | Run replay to generate test data | HAF, hive |
| `templates/haf_app_testing.gitlab-ci.yml` | HAF app sync/test templates | balance_tracker, reputation_tracker |
| Docker images | docker-builder, docker-dind, ci-base-image, emsdk | All projects |

### Still Duplicated

| Pattern | Currently In | Duplication Level |
|---------|--------------|-------------------|
| Database wait loop | HAF, HAF apps | High - copy-pasted everywhere |
| Cache-manager fetch pattern | HAF, HAF apps | High - 5+ line block repeated |
| Stale cache cleanup | HAF | Medium - similar patterns |
| Service health checks | HAF apps | Medium |
| `.prepare_haf_data_5m` template | HAF | High - needed by HAF apps |
| `.prepare_haf_image` template | HAF | Medium |
| `.wait-for-haf-postgres` template | HAF | High - duplicated in apps |

## Phase 1: Script Consolidation (Completed)

### Completed
- [x] Move `prepare_data_and_shm_dir.sh` to common-ci-configuration
- [x] Add symlink fallback for cross-device hardlinks
- [x] Move `build_data.sh` to common-ci-configuration
- [x] Update hive to fetch `build_data.sh` from common-ci-configuration
- [x] Update HAF symlinks to fetch scripts via curl

### Migration Path for Consumers

**Before (hive/HAF using local scripts):**
```bash
"$SCRIPTPATH/prepare_data_and_shm_dir.sh" --data-base-dir="$DATA_CACHE" ...
```

**After (fetch from common-ci-configuration):**
```bash
PREPARE_SCRIPT="/tmp/prepare_data_and_shm_dir.sh"
curl -fsSL "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/develop/scripts/prepare_data_and_shm_dir.sh" -o "$PREPARE_SCRIPT"
chmod +x "$PREPARE_SCRIPT"
"$PREPARE_SCRIPT" --data-base-dir="$DATA_CACHE" ...
```

## Phase 2: Flatten Include Hierarchy (Completed)

Move HAF-specific templates to common-ci-configuration so all projects can include directly.

### Completed
- [x] Create `templates/haf_data_preparation.gitlab-ci.yml` with `.prepare_haf_data_5m` and `.prepare_haf_image`
- [x] Update HAF to include directly from common-ci-configuration (MR !761)
- [x] Update balance_tracker to include directly from common-ci-configuration (MR !260)
- [x] Update reputation_tracker to include directly from common-ci-configuration (MR !142)
- [x] Fix HAF_COMMIT variable inheritance in `.haf_commit_validation` template (MR !138)
- [x] Add default HIVE_SCRIPTS_REF to `.prepare_haf_image` template (MR !139)
- [x] Fix sync save cache to handle symlinks in read-only locations (MR !140)

### Goal

Flatten the 4-level hierarchy to a single level:

```
CURRENT:                              PROPOSED:
HAF apps → HAF → hive → common-ci    HAF apps → common-ci-configuration
                                      HAF → common-ci-configuration
                                      hive → common-ci-configuration
```

### Templates to Move

| Template | Currently In | Move To | Notes |
|----------|-------------|---------|-------|
| `.prepare_haf_data_5m` | HAF | `templates/haf_data_preparation.gitlab-ci.yml` | Core data prep job |
| `.prepare_haf_image` | HAF | `templates/haf_data_preparation.gitlab-ci.yml` | Image building |
| `.wait-for-haf-postgres` | HAF | `templates/haf_app_testing.gitlab-ci.yml` | Already have similar |
| `.wait-for-haf-postgres-with-nfs-extraction` | HAF | `templates/haf_app_testing.gitlab-ci.yml` | NFS fallback variant |
| `.docker_image_builder_job` | hive | Already in common-ci | ✓ Done |

### Migration Steps

1. **Create `templates/haf_data_preparation.gitlab-ci.yml`**
   - Move `.prepare_haf_data_5m` with all cache logic
   - Move `.prepare_haf_image`
   - Parameterize image registry URLs

2. **Update HAF to include from common-ci-configuration**
   ```yaml
   # Before (HAF .gitlab-ci.yml)
   include:
     - project: 'hive/hive'
       ref: 8d24f312...
       file: '/scripts/ci-helpers/prepare_data_image_job.yml'

   # After
   include:
     - project: 'hive/common-ci-configuration'
       ref: develop
       file: '/templates/haf_data_preparation.gitlab-ci.yml'
   ```

3. **Update HAF apps to include directly from common-ci-configuration**
   ```yaml
   # Before (balance_tracker .gitlab-ci.yml)
   include:
     - project: 'hive/haf'
       ref: 1ac5d12...
       file: '/scripts/ci-helpers/prepare_data_image_job.yml'

   # After
   include:
     - project: 'hive/common-ci-configuration'
       ref: develop
       file: '/templates/haf_data_preparation.gitlab-ci.yml'
       file: '/templates/haf_app_testing.gitlab-ci.yml'
   ```

4. **Remove deprecated includes from hive and HAF**

### Benefits

- **Single version to track**: All projects pin one common-ci-configuration ref
- **Faster bug fixes**: Fix once in common-ci, all projects get it
- **No cascading MRs**: Updates don't require touching intermediate repos
- **Cleaner dependencies**: HAF apps don't need to know about hive internals

### What Remains Project-Specific

| Project | Project-Specific Config |
|---------|------------------------|
| hive | `run_hived_img.sh`, block_log sources, config.ini |
| HAF | PostgreSQL access rules, HAF-specific test scripts |
| HAF apps | App schema names, sync scripts, test configurations |

## Phase 3: Reusable YAML Blocks (Completed)

Create `!reference`-able script blocks for common patterns.

### Completed Templates

| Template | Location | Description |
|----------|----------|-------------|
| `.fetch_cache_manager` | `haf_app_testing.gitlab-ci.yml` | Fetch cache-manager.sh script |
| `.haf_app_wait_for_postgres` | `haf_app_testing.gitlab-ci.yml` | Wait for PostgreSQL (DinD compatible) |
| `.haf_app_wait_for_postgrest` | `haf_app_testing.gitlab-ci.yml` | Wait for PostgREST with DNS fix (MR !146) |
| `.cleanup_stale_cache` | `cache_cleanup.gitlab-ci.yml` | Clean stale cache with permission issues (MR !146) |
| `.cleanup_cache_manual_template` | `cache_cleanup.gitlab-ci.yml` | Manual cache cleanup job |
| `.cleanup_old_cache_template` | `cache_cleanup.gitlab-ci.yml` | Age-based cache cleanup |

### Example Usage

#### `.fetch-cache-manager`
```yaml
.fetch-cache-manager:
  script:
    - |
      CACHE_MANAGER="/tmp/cache-manager.sh"
      if [[ ! -x "$CACHE_MANAGER" ]]; then
        curl -fsSL "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/develop/scripts/cache-manager.sh" -o "$CACHE_MANAGER"
        chmod +x "$CACHE_MANAGER"
      fi
```

**Usage:**
```yaml
my-job:
  before_script:
    - !reference [.fetch-cache-manager, script]
    - "$CACHE_MANAGER" get "haf" "$HAF_COMMIT" "/cache/haf_data"
```

#### `.wait-for-postgres`
```yaml
.wait-for-postgres:
  variables:
    POSTGRES_HOST: "haf-instance"
    POSTGRES_PORT: "5432"
    POSTGRES_TIMEOUT: "120"
  script:
    - |
      echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
      for i in $(seq 1 $POSTGRES_TIMEOUT); do
        if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q 2>/dev/null; then
          echo "PostgreSQL ready after ${i}s"
          break
        fi
        sleep 1
      done
```

**Usage:**
```yaml
my-test-job:
  variables:
    POSTGRES_HOST: "my-db"
  before_script:
    - !reference [.wait-for-postgres, script]
```

#### `.cleanup-stale-cache`
```yaml
.cleanup-stale-cache:
  script:
    - |
      CACHE_PATH="${CACHE_DIR:-/cache/data}"
      if [[ -d "$CACHE_PATH" ]]; then
        if ! touch "$CACHE_PATH/.write_test" 2>/dev/null; then
          echo "Stale cache with permission issues, cleaning up..."
          sudo rm -rf "$CACHE_PATH" || rm -rf "$CACHE_PATH" || true
        else
          rm -f "$CACHE_PATH/.write_test"
        fi
      fi
```

## Phase 4: HAF App Template Expansion

Extend `templates/haf_app_testing.gitlab-ci.yml` with more building blocks.

### Current Templates
- `.haf_app_detect_changes` - Skip sync if only tests changed
- `.haf_app_sync_*` - Sync job components
- `.haf_app_smart_cache_lookup` - QUICK_TEST support

### Proposed Additions

#### `.haf_app_service_container`
Standard HAF service container configuration:
```yaml
.haf_app_service_container:
  services:
    - name: ${HAF_IMAGE_NAME}
      alias: haf-instance
      variables:
        PG_ACCESS: "${HAF_DB_ACCESS}"
        DATA_SOURCE: "${HAF_DATA_CACHE_LOCAL}"
      command: ["--execute-maintenance-script=${HAF_SOURCE_DIR}/scripts/maintenance-scripts/sleep_infinity.sh"]
```

#### `.haf_app_tavern_test`
Complete Tavern test job template:
```yaml
.haf_app_tavern_test:
  extends:
    - .haf_app_service_container
    - .wait-for-postgres
  variables:
    TAVERN_VERSION: "2.0.0"
    PYTEST_WORKERS: "4"
  script:
    - pip install tavern==${TAVERN_VERSION}
    - pytest -n ${PYTEST_WORKERS} tests/
```

## Phase 5: Documentation and Migration Guides

### Per-Project Migration Guides

| Project | Priority | Complexity | Notes |
|---------|----------|------------|-------|
| balance_tracker | High | Low | Already uses `.haf_app_*` templates |
| reputation_tracker | High | Low | Already uses `.haf_app_*` templates |
| hafah | Medium | Medium | Different test patterns |
| haf_block_explorer | Medium | Medium | Custom sync process |
| hivemind | Low | High | Complex, multiple test types |
| nft_tracker | Low | Low | Simpler patterns |

### Migration Checklist

For each project:
1. [ ] Identify duplicated CI patterns
2. [ ] Map to common-ci-configuration templates
3. [ ] Update includes to reference common-ci-configuration
4. [ ] Replace inline scripts with `!reference` tags
5. [ ] Test pipeline with QUICK_TEST mode
6. [ ] Full pipeline validation
7. [ ] Update project CLAUDE.md

## Versioning Strategy

### Script Versioning
- Scripts fetched via curl use `develop` branch by default
- For stability, projects can pin to specific commits:
  ```bash
  curl -fsSL "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/abc123/scripts/cache-manager.sh"
  ```

### Template Versioning
- Templates included via GitLab include use refs:
  ```yaml
  include:
    - project: 'hive/common-ci-configuration'
      ref: 'v1.0.0'  # or commit SHA
      file: '/templates/haf_app_testing.gitlab-ci.yml'
  ```

## Success Metrics

1. **Reduced Duplication**: Each consolidated pattern should exist in only one place
2. **Faster Onboarding**: New HAF apps should be able to copy a template CI config
3. **Easier Maintenance**: Bug fixes applied once, benefit all projects
4. **Consistent Behavior**: Same patterns behave identically across projects

## Timeline

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Script Consolidation | Completed |
| Phase 2 | Flatten Include Hierarchy | Completed |
| Phase 3 | Reusable YAML Blocks | Completed |
| Phase 4 | HAF App Template Expansion | Planning |
| Phase 5 | Documentation & Migration Guides | Ongoing |
