# HAF Application Testing Templates

Reusable CI templates for applications that depend on HAF (Hive Application Framework).

## Overview

These templates standardize common CI patterns found across HAF-dependent applications like HAfAH, reputation_tracker, balance_tracker, and hivemind. They provide:

- **Change detection** with automatic skip logic
- **Cache management** with NFS backing
- **Docker-in-Docker test infrastructure**
- **Tavern API testing** framework

## Quick Start

Include the templates in your `.gitlab-ci.yml`:

```yaml
include:
  - project: 'hive/common-ci-configuration'
    ref: develop
    file: '/templates/haf_app_testing.gitlab-ci.yml'

stages:
  - detect
  - build
  - sync
  - test
  - cleanup

detect_changes:
  extends: .haf_app_detect_changes
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"

api_tests:
  extends: .haf_app_tavern_tests
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"
    HAF_APP_CACHE_KEY: "${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}"
    TEST_DIR: "tests/api_tests"
```

## Templates Reference

### .fetch_cache_manager

Sets up the cache-manager.sh script for use in jobs.

```yaml
my_job:
  extends: .fetch_cache_manager
  script:
    - $CACHE_MANAGER get haf $HAF_COMMIT /data
```

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_MANAGER` | `/tmp/cache-manager.sh` | Path where script is saved |
| `CACHE_MANAGER_REF` | `develop` | Git ref to fetch from |

### .haf_app_detect_changes

Analyzes changed files to determine if full HAF sync can be skipped. When only tests, docs, or CI config change, cached HAF data can be reused.

```yaml
detect_changes:
  extends: .haf_app_detect_changes
  variables:
    HAF_APP_CACHE_TYPE: "haf_btracker_sync"
    HAF_APP_SKIP_PATTERNS: "^tests/|^docs/|\.md$"
```

**Required Variables:**
| Variable | Description |
|----------|-------------|
| `HAF_APP_CACHE_TYPE` | Cache type name (e.g., `haf_btracker_sync`) |

**Optional Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `HAF_APP_SKIP_PATTERNS` | `^tests/\|^docs/\|\.md$\|^README\|^CHANGELOG\|^LICENSE\|^CLAUDE\|^\.gitlab-ci` | Regex patterns for files that don't require sync |

**Output (dotenv artifact):**
| Variable | Description |
|----------|-------------|
| `AUTO_SKIP_SYNC` | `"true"` if sync can be skipped |
| `AUTO_CACHE_KEY` | Cache key to use |
| `AUTO_HAF_COMMIT` | HAF commit from discovered cache |

### .haf_commit_validation

Ensures HAF_COMMIT variable matches include ref and submodule commit.

```yaml
validate_haf:
  extends: .haf_commit_validation
  variables:
    HAF_COMMIT: "1ac5d12439b9cca33e6db383adc59967cae75fc4"
    HAF_INCLUDE_REF: "1ac5d12439b9cca33e6db383adc59967cae75fc4"
```

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `HAF_COMMIT` | (required) | Expected HAF commit |
| `HAF_INCLUDE_REF` | (optional) | Should match include `ref:` |
| `HAF_SUBMODULE_PATH` | `haf` | Path to HAF submodule |

### .haf_app_dind_test_base

Base template for running tests with Docker Compose in a DinD environment.

```yaml
my_tests:
  extends: .haf_app_dind_test_base
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"
    HAF_APP_CACHE_KEY: "${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}"
    COMPOSE_FILE: "docker/docker-compose-test.yml"
  script:
    - pytest tests/
```

**Required Variables:**
| Variable | Description |
|----------|-------------|
| `HAF_APP_CACHE_TYPE` | Cache type to extract |
| `HAF_APP_CACHE_KEY` | Cache key (usually from detect_changes or sync job) |

**Optional Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `HAF_DATA_DIRECTORY` | `${CI_PROJECT_DIR}/.haf-data` | Where to extract cache |
| `COMPOSE_FILE` | `docker/docker-compose-test.yml` | Docker Compose file path |
| `COMPOSE_PROJECT_NAME` | `haf-test-${CI_JOB_ID}` | Compose project name |
| `HAF_EXTRACT_TIMEOUT` | `300` | Timeout for cache extraction |

**Features:**
- Automatic cache extraction via cache-manager
- Docker Compose service orchestration
- Health check waiting
- Automatic log collection and cleanup

### .haf_app_tavern_tests

Specialized template for Tavern-based API testing.

```yaml
api_tests:
  extends: .haf_app_tavern_tests
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"
    HAF_APP_CACHE_KEY: "${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}"
    TEST_DIR: "tests/api_tests"
    PYTEST_WORKERS: "8"
```

**Optional Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `PYTEST_WORKERS` | `4` | Number of parallel pytest workers |
| `TAVERN_VERSION` | `2.2.0` | Tavern package version |
| `PYTEST_ARGS` | (empty) | Additional pytest arguments |
| `TEST_DIR` | `tests/api_tests` | Directory containing tests |
| `JUNIT_REPORT` | `tavern-results.xml` | JUnit output filename |

### .haf_app_cache_cleanup

Manual cleanup job for local cache data.

```yaml
cleanup_cache:
  extends: .haf_app_cache_cleanup
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"
```

## Lint Templates

### .haf_app_lint_bash

Bash script linting with shellcheck. Generates checkstyle XML reports for GitLab integration.

```yaml
lint_bash:
  extends: .haf_app_lint_bash
  variables:
    LINT_SCRIPTS_DIR: "scripts"  # Optional, defaults to "scripts"
```

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `LINT_SCRIPTS_DIR` | `scripts` | Directory to search for .sh files |

**Artifacts:**
- `shellcheck.xml` - Checkstyle format report
- `shellcheck.html` - HTML formatted report

### .haf_app_lint_sql

SQL linting with sqlfluff for PostgreSQL. Outputs YAML format report.

```yaml
lint_sql:
  extends: .haf_app_lint_sql
  variables:
    SQLFLUFF_CONFIG: ".sqlfluff"  # Optional
    SQL_PATHS: "db/"              # Optional, defaults to "."
```

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `SQLFLUFF_DIALECT` | `postgres` | SQL dialect |
| `SQLFLUFF_CONFIG` | (empty) | Path to sqlfluff config file |
| `SQL_PATHS` | `.` | Paths to lint |

**Artifacts:**
- `sqlfluff.yaml` - YAML format lint report

## Test Helper Templates

These templates provide composable scripts for test job setup. Use with `!reference` to build your before_script:

```yaml
my_tests:
  before_script:
    - !reference [.haf_app_wait_for_postgres, script]
    - !reference [.haf_app_wait_for_postgrest, script]
    - # Your additional setup
```

### .haf_app_wait_for_postgres

Waits for PostgreSQL to be ready. Works with both DinD (docker-compose) and service container patterns.

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `HAF_SERVICE_TIMEOUT` | `300` | Max wait time in seconds |
| `HAF_PG_HOST` | (auto-detect) | PostgreSQL host (for service containers) |
| `HAF_PG_PORT` | `5432` | PostgreSQL port |
| `HAF_PG_USER` | `haf_admin` | PostgreSQL user |

### .haf_app_wait_for_postgrest

Waits for PostgREST API to be ready.

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `PGRST_TIMEOUT` | `120` | Max wait time in seconds |
| `PGRST_HOST` | `http://postgrest:3000` | PostgREST URL |

### .haf_app_wait_for_sync

Waits for HAF to sync to a specific block number. Useful for tests that require data at a specific state.

**Variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `HAF_TARGET_BLOCK` | `5000000` | Target block number |
| `HAF_SYNC_WAIT_TIMEOUT` | `1800` | Max wait time (30 min) |

### .haf_app_extract_test_cache

Extracts cached sync data for test jobs. Simplified version of cache extraction.

**Required Variables:**
| Variable | Description |
|----------|-------------|
| `APP_SYNC_CACHE_TYPE` | Cache type (e.g., `haf_btracker_sync`) |
| `APP_CACHE_KEY` | Cache key |
| `HAF_DATA_DIRECTORY` | Target extraction directory |

## Service Container Configuration

Reference templates for GitLab CI service container configuration.

**Note:** GitLab CI services cannot use `!reference` directly. Copy these patterns into your services section.

### .haf_service_config

HAF instance service configuration variables:

```yaml
my_tests:
  variables:
    HAF_PG_ACCESS: "host all all 0.0.0.0/0 trust"
    HAF_STOP_BLOCK: "5000024"
    HAF_SERVICE_TIMEOUT: "300"
  services:
    - name: ${HAF_IMAGE_NAME}
      alias: haf-instance
      command: ["--stop-at-block=${HAF_STOP_BLOCK}", "--skip-hived"]
```

### .postgrest_service_config

PostgREST service configuration variables:

```yaml
my_tests:
  variables:
    PGRST_DB_URI: "postgres://haf_admin@haf-instance:5432/haf_block_log"
    PGRST_DB_ANON_ROLE: "haf_app_user"
    PGRST_DB_SCHEMA: "btracker_app"  # Override per-app
  services:
    - name: postgrest/postgrest:v12.0.2
      alias: postgrest
```

## Rule Templates

### .skip_on_quick_test

Skip job when `QUICK_TEST=true`:

```yaml
sync:
  extends: .skip_on_quick_test
  script:
    - # Full sync logic
```

### .skip_on_auto_skip

Skip job when `AUTO_SKIP_SYNC=true` (from detect_changes):

```yaml
sync:
  extends: .skip_on_auto_skip
  script:
    - # Full sync logic
```

### .skip_on_cached_data

Skip job when either skip condition is true:

```yaml
sync:
  extends: .skip_on_cached_data
  script:
    - # Full sync logic
```

## Early Exit Templates

Early exit templates let jobs run but exit immediately when no work is needed. Unlike skip rules, these jobs show as "passed" in the pipeline.

### .haf_app_early_exit_on_skip

Exit early when `AUTO_SKIP_SYNC=true` or `AUTO_SKIP_BUILD=true`:

```yaml
build_something:
  script:
    - !reference [.haf_app_early_exit_on_skip, script]
    - # Actual build logic (only runs if not skipped)
```

### .haf_app_early_exit_on_quick_test

Exit early when `QUICK_TEST=true`:

```yaml
prepare_data:
  script:
    - !reference [.haf_app_early_exit_on_quick_test, script]
    - # Data preparation (skipped in quick test mode)
```

### .haf_app_early_exit_on_cached

Exit early on either condition (QUICK_TEST or AUTO_SKIP):

```yaml
sync:
  script:
    - !reference [.haf_app_early_exit_on_cached, script]
    - # Sync logic (skipped if using cached data)
```

## Skip Pattern Presets

Pre-configured skip patterns for common HAF app types.

### .haf_app_skip_patterns_standard

Standard patterns for HAF apps without a GUI:

```yaml
detect_changes:
  extends:
    - .haf_app_detect_changes
    - .haf_app_skip_patterns_standard
```

Skips: `tests/`, `docs/`, `*.md`, `README`, `CHANGELOG`, `LICENSE`, `CLAUDE`, `.gitlab-ci`

### .haf_app_skip_patterns_with_gui

For HAF apps with a frontend GUI:

```yaml
detect_changes:
  extends:
    - .haf_app_detect_changes
    - .haf_app_skip_patterns_with_gui
```

Skips: All standard patterns plus `gui/`

## Quick Test Mode

For rapid iteration, set these variables to skip full HAF sync:

```yaml
# In pipeline variables:
QUICK_TEST: "true"
QUICK_TEST_HAF_COMMIT: "abc123..."
```

Find available caches:
```bash
ssh hive-builder-10 'ls -lt /nfs/ci-cache/haf_myapp_sync/*.tar | head -5'
```

## Cache Key Format

Cache keys follow the pattern: `<HAF_COMMIT>_<APP_COMMIT>`

Example: `1ac5d12439b9cca33e6db383adc59967cae75fc4_a1b2c3d`

This allows:
- Same HAF data to be shared when only app code changes
- Full rebuild when HAF commit changes
- Easy identification of cache contents

## Migration Guide

### Step 1: Add Variable Aliases

Add these global variables for template compatibility:

```yaml
variables:
  # Aliases for common-ci templates
  APP_SYNC_CACHE_TYPE: "${MY_APP_SYNC_CACHE_TYPE}"
  APP_CACHE_KEY: "${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}"
  HAF_APP_SCHEMA: "myapp"
```

### Step 2: Migrate DinD Test Jobs

**Before (reputation_tracker, 168 lines):**
```yaml
.test-with-docker-compose:
  extends: .docker_image_builder_job_template
  before_script:
    - !reference [.docker_image_builder_job_template, before_script]
    - docker login -u "$CI_REGISTRY_USER" -p "$CI_REGISTRY_PASSWORD" $CI_REGISTRY
    - |
      # 60+ lines of cache extraction
      CACHE_MANAGER="/tmp/cache-manager.sh"
      curl -fsSL "..." -o "$CACHE_MANAGER"
      chmod +x "$CACHE_MANAGER"
      "$CACHE_MANAGER" get "${CACHE_TYPE}" "${CACHE_KEY}" "${JOB_DIR}"
      # ... blockchain handling, compose startup, service wait
  after_script:
    - # 30+ lines of cleanup
```

**After (8 lines):**
```yaml
.test-with-docker-compose:
  extends: .haf_app_dind_complete_test
  image: registry.gitlab.syncad.com/hive/reputation_tracker/ci-runner:docker-24.0.1-8
  variables:
    COMPOSE_OPTIONS_STRING: "--file docker-compose-test.yml --ansi never"
```

### Step 3: For Apps with Multiple DinD Jobs

If your app has several DinD test jobs (like balance_tracker), create a project-specific template:

```yaml
# Project-specific template consolidating common patterns
.myapp-dind-test:
  extends: .docker_image_builder_job_template
  stage: test
  image: my-ci-runner-image
  variables:
    HAF_DATA_DIRECTORY: ${CI_PROJECT_DIR}/${CI_JOB_ID}/datadir
    MYAPP_TEST_CACHE_TYPE: "${MYAPP_SYNC_CACHE_TYPE}"
  before_script:
    - !reference [.docker_image_builder_job_template, before_script]
    - !reference [.haf_app_extract_test_cache, script]
    # ... compose startup, service wait
  after_script:
    # ... cleanup

# Simplified jobs
regression-test:
  extends: .myapp-dind-test
  script:
    - ./run-tests.sh

pattern-test:
  extends: .myapp-dind-test
  variables:
    WAIT_FOR_POSTGREST: "true"
  script:
    - pytest tests/
```

### Real Migration Examples

| Project | MR | Before | After | Reduction |
|---------|-----|--------|-------|-----------|
| reputation_tracker | !143 | 168 lines | 8 lines | -160 lines |
| balance_tracker | !261 | 445 lines (3 jobs) | 91 lines | -354 lines |

### From inline cache-manager fetch

**Before:**
```yaml
before_script:
  - CACHE_MANAGER=/tmp/cache-manager.sh
  - curl -fsSL "https://gitlab.syncad.com/..." -o "$CACHE_MANAGER"
  - chmod +x "$CACHE_MANAGER"
```

**After:**
```yaml
extends: .fetch_cache_manager
```

### From custom change detection

**Before:**
```yaml
detect_changes:
  script:
    - # 80+ lines of detection logic
```

**After:**
```yaml
detect_changes:
  extends: .haf_app_detect_changes
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"
```

### From custom DinD test jobs

**Before:**
```yaml
tests:
  image: docker-builder
  services:
    - docker:dind
  before_script:
    - # 50+ lines of setup
```

**After:**
```yaml
tests:
  extends: .haf_app_dind_test_base
  variables:
    HAF_APP_CACHE_TYPE: "haf_myapp_sync"
    HAF_APP_CACHE_KEY: "$CACHE_KEY"
```

## Best Practices

1. **Use consistent cache type names**: Prefix with `haf_` for automatic pgdata permission handling

2. **Pin CACHE_MANAGER_REF in production**: Use a specific commit or tag rather than `develop`

3. **Set appropriate timeouts**: Override `HAF_SYNC_TIMEOUT` and `HAF_TEST_TIMEOUT` for your workload

4. **Use proper runner tags**: Jobs using cache need `data-cache-storage` and optionally `fast`

5. **Validate HAF commit**: Use `.haf_commit_validation` to catch version mismatches early

## Troubleshooting

### Cache miss when expected hit

1. Check cache type matches exactly (case-sensitive)
2. Verify cache key format matches expected pattern
3. Check NFS availability: `mountpoint /nfs/ci-cache`

### Services not starting

1. Check compose file path is correct
2. Verify image names and tags
3. Check container logs in artifacts

### Slow cache extraction

This is expected for large HAF caches (~19GB). Use `fast` runner tag for better performance.

### Permission errors with pgdata

Ensure cache type starts with `haf_` for automatic permission handling, or manually fix:
```bash
sudo chown -R 105:105 /path/to/pgdata
sudo chmod 700 /path/to/pgdata
```
