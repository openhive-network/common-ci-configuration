# HAF App Tools

Shared utilities for HAF (Hive Application Framework) applications.

These tools were previously located in the `haf` repository and required apps to include `haf` as a submodule. By moving them here, apps can fetch them at runtime without the heavy submodule dependency.

## Contents

### Scripts (`scripts/`)

| Script | Purpose |
|--------|---------|
| `common.sh` | Shared bash utilities: `log_exec_params`, `do_clone_commit`, `do_clone_branch` |
| `create_haf_app_role.sh` | Creates HAF application PostgreSQL roles with proper group membership |
| `copy_datadir.sh` | Copies HAF data directories with NFS cache fallback and symlink fixing |

### Python (`python/`)

| File | Purpose |
|------|---------|
| `process_openapi.py` | Generates SQL types/functions and nginx rewrite rules from OpenAPI YAML embedded in SQL comments |
| `requirements.txt` | Python dependencies for `process_openapi.py` |

### Config (`config/`)

| File | Purpose |
|------|---------|
| `config_5M.ini` | Standard HAF (hived+postgres) configuration for 5M block replay testing |
| `hived_config_5M.ini` | Standalone hived configuration for 5M block replay (comparison tests) |

## Usage

### Fetching at Runtime (Recommended for CI)

```yaml
variables:
  COMMON_CI_REF: "develop"  # or pin to specific commit
  COMMON_CI_URL: "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/${COMMON_CI_REF}"

.fetch_haf_app_tools:
  before_script:
    - mkdir -p /tmp/haf-app-tools
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/common.sh" -o /tmp/haf-app-tools/common.sh
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/create_haf_app_role.sh" -o /tmp/haf-app-tools/create_haf_app_role.sh
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/copy_datadir.sh" -o /tmp/haf-app-tools/copy_datadir.sh
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/python/process_openapi.py" -o /tmp/haf-app-tools/process_openapi.py
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/config/config_5M.ini" -o /tmp/haf-app-tools/config_5M.ini
    - curl -fsSL "${COMMON_CI_URL}/haf-app-tools/config/hived_config_5M.ini" -o /tmp/haf-app-tools/hived_config_5M.ini
    - chmod +x /tmp/haf-app-tools/*.sh
```

### Using Scripts

```bash
# Create HAF app role
/tmp/haf-app-tools/create_haf_app_role.sh \
    --postgres-url="postgresql://haf_admin@localhost/haf_block_log" \
    --haf-app-account="myapp"

# Process OpenAPI
pip install -r /tmp/haf-app-tools/requirements.txt
python3 /tmp/haf-app-tools/process_openapi.py output_dir/ endpoints/*.sql
```

## Migration from haf Submodule

See [docs/haf-app-tools-migration.md](../docs/haf-app-tools-migration.md) for detailed migration instructions.

## Apps Using These Tools

- **balance_tracker** - Uses `process_openapi.py`, `config_5M.ini`
- **haf_block_explorer** - Uses `process_openapi.py`, `config_5M.ini`
- **hafah** - Uses `process_openapi.py`, `common.sh`, `create_haf_app_role.sh`, `config_5M.ini`
- **hivemind** - Uses `common.sh`, `create_haf_app_role.sh`, `copy_datadir.sh`, `config_5M.ini`

## Using Pre-built Images (No Submodules)

HAF apps can eliminate the HAF/hive submodule dependency entirely by using pre-built
images from the GitLab registries:

```yaml
variables:
  # Specify commits (full SHA for cache keys, short for image tags)
  HAF_COMMIT: "1edae265e18b96245a3a77e3d937186996dbf8b5"
  HIVE_COMMIT: "1179c5456dbb6be65c73178eb53d2e02223de3a2"

  # Use pre-built images from registry
  HAF_IMAGE_NAME: "registry.gitlab.syncad.com/hive/haf:${HAF_COMMIT:0:8}"
  HIVED_IMAGE_NAME: "registry.gitlab.syncad.com/hive/hive:${HIVE_COMMIT:0:8}"

include:
  - project: 'hive/common-ci-configuration'
    ref: develop
    file:
      - '/templates/haf_data_preparation.gitlab-ci.yml'
      - '/templates/haf_app_testing.gitlab-ci.yml'
```

### Finding the Hive Commit for a HAF Commit

The hive commit that corresponds to a HAF commit can be found by querying HAF's
submodule pointer:

```bash
# From a HAF checkout
git -C /path/to/haf ls-tree <haf-commit> hive
# Output: 160000 commit <hive-commit>    hive

# Or via GitLab API (if commit is on a branch)
curl -s "https://gitlab.syncad.com/api/v4/projects/323/repository/tree?path=hive&ref=<haf-commit>"
```

### Available Templates

The `haf_data_preparation.gitlab-ci.yml` template provides:

| Template | Purpose |
|----------|---------|
| `.prepare_haf_data_5m` | Prepare HAF data with caching (uses HAF image) |
| `.prepare_hived_data_5m` | Prepare standalone hived data (uses hived image) |
| `.wait-for-haf-postgres` | Wait for PostgreSQL service |

See `templates/haf_data_preparation.gitlab-ci.yml` for full documentation.
