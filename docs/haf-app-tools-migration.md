# HAF App Tools Migration Plan

This document outlines the migration of shared HAF application utilities from the `haf` repository to `common-ci-configuration/haf-app-tools/`.

## Motivation

Currently, HAF applications (balance_tracker, haf_block_explorer, hafah, hivemind) include the `haf` repo as a submodule primarily to access:
- Shared scripts (`common.sh`, `create_haf_app_role.sh`, `copy_datadir.sh`)
- OpenAPI processor (`process_openapi.py`)
- Config files (`config_5M.ini`)
- Test infrastructure (`tests_api`)

This creates a deep dependency chain: `app → haf → hive → test-tools/tests_api`

By moving the shared utilities to `common-ci-configuration`, apps can:
1. Drop the `haf` submodule entirely
2. Fetch utilities at runtime (like existing `HIVE_SCRIPTS_REF` pattern)
3. Only add `tests_api` as a direct submodule if needed for testing

## Directory Structure

```
common-ci-configuration/
├── haf-app-tools/
│   ├── scripts/
│   │   ├── common.sh               # Shared bash utilities
│   │   ├── create_haf_app_role.sh  # PostgreSQL role setup
│   │   └── copy_datadir.sh         # Data directory copying with NFS fallback
│   ├── python/
│   │   ├── process_openapi.py      # OpenAPI → SQL/nginx generator
│   │   └── requirements.txt        # deepmerge, jsonpointer, pyyaml
│   └── config/
│       └── config_5M.ini           # Standard 5M block HAF config
```

## Files to Migrate

### From `haf/scripts/`

| File | Purpose | Dependencies |
|------|---------|--------------|
| `common.sh` | Utility functions: `log_exec_params`, `do_clone_commit`, `do_clone_branch` | None |
| `create_haf_app_role.sh` | Creates HAF app PostgreSQL roles with proper group membership | Sources `common.sh` |
| `copy_datadir.sh` | Copies data directories with NFS fallback, fixes pg_tblspc symlinks | Uses `cache-manager.sh` (already in common-ci-configuration) |

### From `haf/scripts/`

| File | Purpose | Dependencies |
|------|---------|--------------|
| `process_openapi.py` | Generates SQL types/functions and nginx rewrite rules from OpenAPI YAML in SQL comments | `deepmerge`, `jsonpointer`, `pyyaml` |

### From `haf/docker/`

| File | Purpose | Dependencies |
|------|---------|--------------|
| `config_5M.ini` | Standard hived config for 5M block replay testing | None |

## Required Modifications

### 1. `create_haf_app_role.sh`

Change the source line to fetch `common.sh` at runtime:

```bash
# Before (line 6):
source "$SCRIPTPATH/common.sh"

# After:
COMMON_CI_URL="${COMMON_CI_URL:-https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/develop}"
if [[ ! -f "$SCRIPTPATH/common.sh" ]]; then
    curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/common.sh" -o /tmp/common.sh
    source /tmp/common.sh
else
    source "$SCRIPTPATH/common.sh"
fi
```

### 2. `copy_datadir.sh`

Already fetches `cache-manager.sh` from common-ci-configuration. No changes needed.

### 3. `process_openapi.py`

No changes needed to the script itself. Apps need to install dependencies:
```bash
pip install deepmerge jsonpointer pyyaml
```

## How Apps Will Fetch Tools

### CI Variable Setup

Apps should define in their `.gitlab-ci.yml`:

```yaml
variables:
  # Reference to common-ci-configuration for fetching tools
  COMMON_CI_REF: "develop"  # or pin to a specific commit
  COMMON_CI_URL: "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/${COMMON_CI_REF}"
```

### Fetching Scripts

```yaml
.fetch_haf_app_tools:
  before_script:
    - mkdir -p /tmp/haf-app-tools
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/common.sh" -o /tmp/haf-app-tools/common.sh
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/create_haf_app_role.sh" -o /tmp/haf-app-tools/create_haf_app_role.sh
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/copy_datadir.sh" -o /tmp/haf-app-tools/copy_datadir.sh
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/python/process_openapi.py" -o /tmp/haf-app-tools/process_openapi.py
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/config/config_5M.ini" -o /tmp/haf-app-tools/config_5M.ini
    - chmod +x /tmp/haf-app-tools/*.sh
```

### Using in Jobs

```yaml
some_job:
  extends: .fetch_haf_app_tools
  script:
    - /tmp/haf-app-tools/create_haf_app_role.sh --postgres-url="$POSTGRES_URL" --haf-app-account="myapp"
    - python3 /tmp/haf-app-tools/process_openapi.py output/ endpoints/*.sql
```

## Migration Steps Per App

### balance_tracker

1. **Add `tests_api` submodule** (for `validate_response` module):
   ```bash
   git submodule add ../tests_api.git tests_api
   ```

2. **Update `.gitlab-ci.yml`**:
   - Add `COMMON_CI_REF` variable
   - Change `pip install -e "${CI_PROJECT_DIR}/haf/hive/tests/python/hive-local-tools/tests_api"` to `pip install -e "${CI_PROJECT_DIR}/tests_api"`
   - Change `CONFIG_INI_SOURCE: "$CI_PROJECT_DIR/haf/docker/config_5M.ini"` to fetch from common-ci-configuration

3. **Update `scripts/openapi_rewrite.sh`**:
   - Change `python3 $haf_dir/scripts/process_openapi.py` to use fetched script

4. **Remove haf submodule**:
   ```bash
   git submodule deinit haf
   git rm haf
   rm -rf .git/modules/haf
   ```

5. **Clean up**:
   - Remove haf-related entries from `.gitmodules`
   - Remove pre_get_sources hook logic for haf submodule corruption
   - Remove git safe.directory entries for haf

### haf_block_explorer

Same as balance_tracker, plus:
- Update `submodules/haf` path to new approach
- May need to update nested submodule handling (btracker, hafah, reptracker)

### hafah

1. **Add `tests_api` submodule**
2. **Update Dockerfile.setup**:
   - Change `COPY haf/scripts/common.sh` to fetch at build time
   - Change `COPY haf/scripts/create_haf_app_role.sh` similarly
3. **Update `.gitlab-ci.yml`** as above
4. **Update `scripts/openapi_rewrite.sh`**
5. **Remove haf submodule**

### hivemind

1. **Update `scripts/ci-helpers/build_instance.sh`**:
   - Change `source "$SCRIPTSDIR/../haf/scripts/common.sh"` to fetch from common-ci-configuration
2. **Update `scripts/setup_postgres.sh`**:
   - Change calls to `haf/scripts/create_haf_app_role.sh`
3. **Update `.gitlab-ci.yml`**:
   - Change `CONFIG_INI_SOURCE` and `copy_datadir.sh` references
4. **Note**: hivemind does NOT use `process_openapi.py` or `tests_api`

## Testing the Migration

1. Create branch in common-ci-configuration with haf-app-tools
2. Update one app (e.g., balance_tracker) to use the new approach
3. Run full CI pipeline to verify:
   - Scripts fetch correctly
   - PostgreSQL role creation works
   - OpenAPI processing works
   - Tests pass
4. Once verified, migrate remaining apps

## Rollback Plan

If issues occur, apps can temporarily:
1. Re-add haf submodule
2. Revert CI changes

The haf repository will retain the original scripts during the transition period.

## Timeline

1. **Phase 1**: Add haf-app-tools to common-ci-configuration
2. **Phase 2**: Migrate balance_tracker as pilot
3. **Phase 3**: Migrate remaining apps (hafah, haf_block_explorer, hivemind)
4. **Phase 4**: (Optional) Deprecate scripts in haf/scripts/ with redirect notice
