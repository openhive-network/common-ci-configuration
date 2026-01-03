# HAF App Testing Templates

Reusable CI templates for HAF-dependent applications (balance_tracker, reputation_tracker, hafah, hivemind).

## Overview

The `templates/haf_app_testing.gitlab-ci.yml` provides composable building blocks for common CI patterns across HAF applications. Apps can use `!reference` tags to include only the pieces they need while keeping app-specific logic inline.

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

### Tavern Test Components
- `.tavern_test_variables` - pytest workers, tavern version, directories
- `.tavern_install_deps` - Install tavern in a venv
- `.tavern_run_tests` - Run pytest with tavern
- `.tavern_test_artifacts` - Artifacts for tavern tests

### Skip Rules
- `.skip_on_quick_test` - Skip job when QUICK_TEST=true
- `.skip_on_auto_skip` - Skip job when AUTO_SKIP_SYNC=true
- `.skip_on_cached_data` - Skip on either condition

## Usage Example

```yaml
include:
  - project: 'hive/common-ci-configuration'
    ref: develop
    file: '/templates/haf_app_testing.gitlab-ci.yml'

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

| Application | Branch | Status |
|-------------|--------|--------|
| reputation_tracker | develop | Merged |
| balance_tracker | develop | Merged |
| hafah | - | Not started |
| hivemind | - | Not started |
| hivesense | - | Not started |
| nft_tracker | - | Not started |
| haf_block_explorer | - | Not started |

## Future Plans

### Short-term
1. **Template `.test-with-docker-compose`** - The test job pattern from reputation_tracker that extracts cache, starts compose, waits for services. Could be reused across apps.

2. **Migrate hafah** - hafah has similar patterns but runs pytest inside a docker container rather than in the CI job. May need variant templates.

3. **Migrate hivemind** - More complex app with multiple test types, but could use sync templates.

### Medium-term
4. **Template docker build jobs** - The `.docker-build-template` pattern is similar across apps.

5. **Template prepare_haf_image/prepare_haf_data** - These jobs are nearly identical across apps.

6. **Add cache cleanup templates** - Automated cleanup of old sync caches.

### Long-term
7. **Unified HAF app CI base** - A single extensible template that apps configure rather than compose from pieces.

8. **Cache warming jobs** - Proactive cache population for common HAF commits.

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
