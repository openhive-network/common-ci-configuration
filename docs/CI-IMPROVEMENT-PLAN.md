# CI/CD Architecture Improvement Plan

## Overview

Analysis of CI configurations across hive, haf, hivemind, hafah, balance_tracker, reputation_tracker, and haf_block_explorer repositories.

| Repository | CI Lines | Stages | Template Source | Caching Tiers |
|------------|----------|--------|-----------------|---------------|
| **hive** | 1,979 | 6 | common-ci-configuration + local | 3 (NFS, local, Docker) |
| **haf** | 1,397 | 7 | common-ci-configuration + hive | 2 (NFS, local) |
| **hivemind** | ~1,000 | 9 | common-ci-configuration + haf | 2 (NFS, local) |
| **hafah** | 872 | 5 | common-ci-configuration + haf | 2 (NFS, local) |
| **balance_tracker** | 1,620 | 7 | common-ci-configuration + haf | 2 (NFS, local) |
| **reputation_tracker** | ~1,200 | 7 | common-ci-configuration + haf | 2 (NFS, local) |
| **haf_block_explorer** | ~1,100 | 7 | common-ci-configuration + haf | 2 (NFS, local) |

---

## Shared Patterns (Already Consistent)

### 1. Template Inheritance Hierarchy
All repos use a consistent template chain:
```
common-ci-configuration (base templates)
    ↓
hive (blockchain-specific templates)
    ↓
haf (HAF app templates: prepare_data_image_job.yml)
    ↓
Application repos (balance_tracker, hafah, etc.)
```

### 2. Two-Tier Caching Strategy
All repos implement:
- **NFS Cache** (`/nfs/ci-cache/...`) - Shared across builders
- **Local Cache** (`/cache/...`) - Per-builder extraction
- **cache-manager.sh** from common-ci-configuration for NFS operations
- Cache keys combining HAF commit + app commit: `${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}`

### 3. Quick Test Mode
All HAF apps support:
```yaml
QUICK_TEST: "true"
QUICK_TEST_HAF_COMMIT: <cached-sha>
```
Skips heavy replay jobs, uses pre-cached data.

### 4. Change Detection & Auto-Skip
All repos have `detect_changes` job that:
- Analyzes changed files
- Sets `AUTO_SKIP_BUILD` / `AUTO_SKIP_SYNC` flags
- Skips heavy jobs for docs/tests/SQL-only changes

### 5. Git Strategy
Consistent across all repos:
```yaml
GIT_STRATEGY: fetch
GIT_DEPTH: 0
GIT_SUBMODULE_STRATEGY: normal  # Manual init for nested submodules
```

### 6. Git Corruption Recovery
All repos have `pre_get_sources` hooks that:
- Remove stale `.git` lock files
- Detect/clean corrupt submodules
- Handle directory-to-submodule transitions

### 7. Docker-in-Docker Pattern
Test jobs use DinD with:
- `DOCKER_HOST: tcp://docker:2375`
- Cache extraction before service startup
- Marker files to prevent race conditions

### 8. Service Container Architecture
All HAF apps use similar service structure:
- **haf-instance**: PostgreSQL + hived indexing
- **app-setup**: Schema installation
- **app/postgrest**: REST API gateway

### 9. Runner Tag Strategy
Consistent tags:
- `public-runner-docker`: General jobs
- `data-cache-storage`: NFS cache access (builders 8-12)
- `fast`: High-performance builders

### 10. HAF Commit Validation
All HAF apps have `validate_haf_commit` job ensuring consistency between:
- `.gitmodules` submodule ref
- `HAF_COMMIT` variable
- `include: ref:` statement

---

## Key Differences

### Pipeline Stage Organization

| Repo | Unique Stages |
|------|---------------|
| **hive** | `static_code_analysis`, `deploy` (mirrornet trigger) |
| **haf** | `build_and_test_phase_1/2` (split build-test phases) |
| **hivemind** | `benchmark`, `collector` (performance tracking) |
| **hafah** | Standard 5-stage (simplest) |
| **balance_tracker** | `sync` separate from `test` |
| **reputation_tracker** | Similar to balance_tracker |
| **haf_block_explorer** | Most complex submodule dependencies (4 nested apps) |

### Submodule Complexity

| Repo | Submodule Depth | Notes |
|------|-----------------|-------|
| **hive** | 1 (test-tools, etc.) | Parent repo |
| **haf** | 2 (hive → test-tools) | Moderately complex |
| **hivemind** | 3 (hafah, reputation_tracker nested HAFs) | Very complex |
| **hafah** | 2 (haf → hive) | Standard HAF app |
| **balance_tracker** | 2 (haf → hive) | Standard HAF app |
| **reputation_tracker** | 2 (haf → hive) | Standard HAF app |
| **haf_block_explorer** | 4 (btracker, hafah, reptracker, each with HAF) | Most complex |

### Cache Key Prefixes (Inconsistent!)

| Repo | NFS Cache Prefix |
|------|------------------|
| **hive** | `/nfs/ci-cache/hive/replay_data_hive_*` |
| **haf** | `/nfs/ci-cache/haf/*` |
| **hivemind** | `/nfs/ci-cache/haf_hivemind_sync/*` |
| **hafah** | `/nfs/ci-cache/hif/*` (**inconsistent naming!**) |
| **balance_tracker** | `/nfs/ci-cache/haf_btracker_sync/*` |
| **reputation_tracker** | `/nfs/ci-cache/haf_reptracker_sync/*` |
| **haf_block_explorer** | `/nfs/ci-cache/haf_hafbe_sync/*` |

---

## Improvement Actions

### Action 1: Create Unified HAF App Template (HIGH PRIORITY)

**Problem**: balance_tracker, reputation_tracker, hafah, and haf_block_explorer have ~70% identical CI code.

**Solution**: Create `templates/haf_app_base.gitlab-ci.yml` with:
- Standard 7-stage pipeline structure
- Pre-configured sync job template
- Unified test job templates (regression, pattern, performance)
- WAX spec + Python API client generation

**Expected reduction**: Each app's CI from ~1000+ lines to ~200-300 lines.

**Implementation**:
1. Extract common patterns from existing apps
2. Create parameterized templates with variables for app-specific config
3. Migrate one app (balance_tracker) as pilot
4. Roll out to remaining apps

### Action 2: Standardize Cache Naming (HIGH PRIORITY)

**Problem**: HAfAH uses `/nfs/ci-cache/hif/` while others use `haf_<app>_sync` pattern.

**Solution**:
- Rename hafah cache to `/nfs/ci-cache/haf_hafah_sync/`
- Document standard naming convention: `haf_<app>_sync`
- Update cache-manager prefixes

**Implementation**:
1. Update hafah CI config
2. Clean up old cache directories
3. Document naming convention

### Action 3: Implement Shared HAF Cache Warming (HIGH PRIORITY)

**Problem**: Each app independently replays HAF 5M blocks, duplicating work.

**Solution**: Create a central "HAF cache warmer" pipeline that:
- Runs nightly or on HAF develop changes
- Pre-warms NFS cache for all apps
- Apps only need to sync their application layer

**Estimated savings**: 80 minutes × 5 apps = 400 minutes per HAF change.

**Implementation**:
1. Create scheduled pipeline in HAF repo
2. Generate caches for mainnet, testnet, mirrornet
3. Update app pipelines to use pre-warmed caches
4. Add cache freshness validation

---

## Additional Improvements (Medium Priority)

### 4. Extract Common Submodule Initialization

**Problem**: Each repo has slightly different `.init_submodules` logic with copy-paste drift.

**Recommendation**: Create `scripts/init_haf_submodules.sh` that:
- Handles arbitrary nesting depth
- Properly resolves relative URLs
- Has consistent corruption detection/recovery

### 5. Centralize Skip Rules

**Problem**: `skip_rules.yml` exists in multiple repos with slight variations.

**Recommendation**: Move to common-ci-configuration with parameterized patterns:
```yaml
include:
  - project: hive/common-ci-configuration
    file: templates/skip_rules.gitlab-ci.yml
variables:
  HAF_APP_SKIP_PATTERNS: "tests/,docs/,*.md,CLAUDE.md"
```

### 6. Add Pipeline Status Dashboard

**Problem**: No unified view of CI health across all repos.

**Recommendation**: Extend existing Grafana dashboard to show:
- Pipeline success rates per repo
- Cache hit rates
- Average sync/test times
- Failed job patterns

---

## Timeline

| Phase | Action | Status |
|-------|--------|--------|
| 1 | Create haf_app_base.gitlab-ci.yml | Pending |
| 1 | Fix hafah cache naming | Pending |
| 2 | Implement HAF cache warmer | Pending |
| 3 | Extract common submodule init | Pending |
| 3 | Centralize skip rules | Pending |
| 4 | Add CI dashboard metrics | Pending |

---

## Success Metrics

- Reduce average HAF app CI config from ~1000 lines to ~300 lines
- Reduce duplicate HAF replay time by 80% via cache warming
- Achieve 100% cache naming consistency
- Zero copy-paste drift in submodule initialization
