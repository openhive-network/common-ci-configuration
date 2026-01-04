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
| `config_5M.ini` | Standard hived configuration for 5M block replay testing |

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
