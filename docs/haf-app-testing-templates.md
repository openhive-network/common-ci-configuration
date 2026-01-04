# HAF App Testing Templates

Reusable CI templates for HAF-dependent applications (balance_tracker, reputation_tracker, hafah, hivemind).

## Overview

The `templates/haf_app_testing.gitlab-ci.yml` provides composable building blocks for common CI patterns across HAF applications. Apps can use `!reference` tags to include only the pieces they need while keeping app-specific logic inline.

## Quick Start

```yaml
include:
  - project: 'hive/common-ci-configuration'
    ref: develop
    file: '/templates/haf_app_testing.gitlab-ci.yml'

variables:
  # Required aliases for templates
  APP_SYNC_CACHE_TYPE: "${MY_APP_SYNC_CACHE_TYPE}"
  APP_CACHE_KEY: "${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}"
  HAF_APP_SCHEMA: "myapp"
```

## Available Templates

### Change Detection
- `.haf_app_detect_changes` - Detects if only tests/docs changed, enabling skip of heavy sync jobs

### Sync Job Components
- `.haf_app_sync_variables` - Common directory and compose variables
- `.haf_app_sync_setup` - Docker login and git safe.directory
- `.haf_app_fetch_haf_cache` - Simple HAF cache lookup (local then NFS)
- `.haf_app_smart_cache_lookup` - Advanced lookup with QUICK_TEST and AUTO_SKIP_SYNC support
- `.haf_app_copy_datadir` - Copy datadir and fix postgres ownership
- `.haf_app_copy_blockchain` - Copy block_log to docker directory
- `.haf_app_sync_shutdown` - PostgreSQL checkpoint, collect logs, compose down
- `.haf_app_sync_save_cache` - Save to local and push to NFS (unconditional)
- `.haf_app_sync_save_cache_conditional` - Only saves if CACHE_HIT is false
- `.haf_app_sync_cleanup` - after_script cleanup
- `.haf_app_sync_artifacts` - Standard artifacts configuration

### DinD Test Templates (Phase 4)
- `.haf_app_dind_test_base` - Base DinD test template with cache extraction and compose
- `.haf_app_extract_test_cache` - Extract sync cache for test jobs
- `.haf_app_wait_for_postgres` - Wait for PostgreSQL with timeout
- `.haf_app_wait_for_postgrest` - Wait for PostgREST with DNS fix

### Tavern Test Components
- `.tavern_test_variables` - pytest workers, tavern version, directories
- `.tavern_install_deps` - Install tavern in a venv
- `.tavern_run_tests` - Run pytest with tavern
- `.tavern_test_artifacts` - Artifacts for tavern tests

### Skip Rules
- `.skip_on_quick_test` - Skip job when QUICK_TEST=true
- `.skip_on_auto_skip` - Skip job when AUTO_SKIP_SYNC=true
- `.skip_on_cached_data` - Skip on either condition

## Migration Examples

### reputation_tracker (MR !143)

**Before:** 168 lines of inline `.test-with-docker-compose`
```yaml
.test-with-docker-compose:
  extends: .docker_image_builder_job_template
  stage: test
  image: registry.gitlab.syncad.com/hive/reputation_tracker/ci-runner:docker-24.0.1-8
  variables:
    HAF_DATA_DIRECTORY: ${CI_PROJECT_DIR}/${CI_JOB_ID}/datadir
    HAF_SHM_DIRECTORY: ${CI_PROJECT_DIR}/${CI_JOB_ID}/shm_dir
    # ... many more lines
  before_script:
    # 60+ lines of cache extraction, compose startup, service wait
  after_script:
    # 30+ lines of cleanup
```

**After:** 8 lines extending templates
```yaml
.test-with-docker-compose:
  extends:
    - .haf_app_dind_complete_test
  image: registry.gitlab.syncad.com/hive/reputation_tracker/ci-runner:docker-24.0.1-8
  variables:
    COMPOSE_OPTIONS_STRING: "--file docker-compose-test.yml --ansi never"
```

### balance_tracker (MR !261)

balance_tracker created a project-specific `.btracker-dind-test` template that consolidates its DinD patterns, then simplified three test jobs.

**Before:** ~445 lines across 3 jobs
```yaml
regression-test:
  extends: .docker_image_builder_job_template
  stage: test
  image: registry.gitlab.syncad.com/hive/balance_tracker/ci-runner:docker-24.0.1-17
  variables:
    HAF_DATA_DIRECTORY: ${CI_PROJECT_DIR}/${CI_JOB_ID}/datadir
    # ... many variables
  before_script:
    # 80+ lines: docker login, cache extraction, blockchain handling,
    # ci.env creation, compose startup, PostgreSQL wait
  script:
    - ./accounts_dump_test.sh --host=docker
  after_script:
    # 20+ lines of cleanup
```

**After:** ~91 lines total
```yaml
# Project-specific base template (consolidates common patterns)
.btracker-dind-test:
  extends: .docker_image_builder_job_template
  stage: test
  image: registry.gitlab.syncad.com/hive/balance_tracker/ci-runner:docker-24.0.1-17
  variables:
    BTRACKER_TEST_CACHE_TYPE: "${BTRACKER_SYNC_CACHE_TYPE}"
    BTRACKER_TEST_CACHE_KEY: "${BTRACKER_CACHE_KEY}"
    WAIT_FOR_POSTGREST: "false"
  # ... before_script, after_script consolidated here

# Simplified job
regression-test:
  extends: .btracker-dind-test
  needs:
    - detect_changes
    - sync
    - docker-setup-docker-image-build
    - prepare_haf_image
  script:
    - cd "${CI_PROJECT_DIR}/tests/account_balances"
      ./accounts_dump_test.sh --host=docker
```

## Sync Job Example

```yaml
sync:
  extends:
    - .docker_image_builder_job_template
    - .haf_app_sync_variables
  variables:
    APP_SYNC_CACHE_TYPE: "${MY_APP_SYNC_CACHE_TYPE}"
    APP_CACHE_KEY: "${MY_APP_CACHE_KEY}"
  before_script:
    - !reference [.haf_app_sync_setup, script]
    - !reference [.haf_app_smart_cache_lookup, script]
  script:
    - # App-specific startup logic
    - !reference [.haf_app_sync_shutdown, script]
    - !reference [.haf_app_sync_save_cache_conditional, script]
  after_script: !reference [.haf_app_sync_cleanup, after_script]
  artifacts: !reference [.haf_app_sync_artifacts, artifacts]
```

## Migrated Applications

| Application | Status | MR | Line Reduction |
|-------------|--------|-----|----------------|
| reputation_tracker | ✅ Done | !143 | 168 → 8 lines |
| balance_tracker | ✅ Done | !261 | 445 → 91 lines (net -216) |
| hafah | Pending | - | - |
| hivemind | Pending | - | - |
| hivesense | Pending | - | - |
| nft_tracker | Pending | - | - |
| haf_block_explorer | Pending | - | - |

## Variable Reference

### Required for sync templates
- `HAF_COMMIT` - HAF submodule commit SHA
- `APP_SYNC_CACHE_TYPE` - App-specific cache type (e.g., "haf_btracker_sync")
- `APP_CACHE_KEY` - Cache key (typically `${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}`)

### From detect_changes (optional)
- `AUTO_SKIP_SYNC` - "true" when only tests/docs changed
- `AUTO_CACHE_HAF_COMMIT` - HAF commit for cached data

### For QUICK_TEST mode
- `QUICK_TEST` - Set to "true" to enable
- `QUICK_TEST_HAF_COMMIT` - HAF commit to use for cache

### For DinD test templates
- `HAF_DATA_DIRECTORY` - Where to extract cache data
- `HAF_SHM_DIRECTORY` - Shared memory directory
- `COMPOSE_OPTIONS_STRING` - Docker compose options
- `WAIT_FOR_POSTGREST` - Set to "true" to wait for PostgREST

## Best Practices

1. **Use consistent cache type prefixes**: Prefix with `haf_` for automatic pgdata permission handling

2. **Create project-specific templates**: If your app has multiple similar DinD jobs, create a local template (like `.btracker-dind-test`) that extends the common templates

3. **Use `!reference` for composability**: Build before_script from template components

4. **Add variable aliases**: Define `APP_SYNC_CACHE_TYPE`, `APP_CACHE_KEY`, `HAF_APP_SCHEMA` at the global level for template compatibility
