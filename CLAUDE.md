# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **common-ci-configuration** - a shared CI/CD template library for Hive blockchain projects. It provides:
- GitLab CI job templates for Docker image building, testing, and publishing
- Pre-built Docker images (emsdk, python, nginx, postgrest, psql, etc.)
- npm/pnpm package build and publish scripts
- Python utilities for GitLab registry management
- Cache management system for CI replay data (`scripts/cache-manager.sh`)

## Documentation

Detailed documentation is available in `docs/`:
- `cache-manager.md` - NFS-backed cache system for HAF/hive replay data
- `common-ci-images.md` - Docker images, their purposes, and Python versions
- `haf-app-testing.md` - Templates for HAF-dependent application testing
- `image-cache-lookup.md` - Scripts for finding pre-built images and avoiding rebuilds
- `npmjs-staged-publishing.md` - Staged (2FA-approved) publishing of @hiveio packages to npmjs: token, variable, approval

## Validation Commands

```bash
# Lint bash scripts (run locally)
shellcheck scripts/**/*.sh

# Lint CI templates
yamllint templates/

# Lint Python scripts
pylint scripts/python/*.py
```

These run automatically in CI during the `validation` stage.

## Docker Image Builds

Images are built using Docker BuildKit with `docker-bake.hcl`:

```bash
# Build a specific target locally
docker buildx bake <target>

# Available targets: benchmark-test-runner, docker-builder, docker-dind,
# python-scripts, tox-test-runner, emsdk, psql, dockerfile, nginx,
# postgrest, alpine, python, python_runtime, python_development,
# python-test-tools, node-test-tools, node-test-tools-pg, node-test-tools-browser
# (node-test-tools-images builds all three node images)
```

Version pinning is managed in `docker-bake.hcl` - update variables there when bumping versions.

## Key Templates

Templates are in `templates/` and are included by downstream projects:

| Template | Purpose |
|----------|---------|
| `docker_image_jobs.gitlab-ci.yml` | Docker image building/cleanup |
| `npm_projects.gitlab-ci.yml` | npm/pnpm package builds |
| `test_jobs.gitlab-ci.yml` | pytest, jmeter, tox test runners |
| `python_projects.gitlab-ci.yml` | Python linting/testing |
| `haf_app_testing.gitlab-ci.yml` | HAF app change detection, DinD testing, Tavern |
| `source_change_detection.gitlab-ci.yml` | Source change detection, skip rules, upstream image lookup |
| `cache-manager.gitlab-ci.yml` | Cache-manager script setup |
| `base.gitlab-ci.yml` | Common job defaults |

## Pipeline Skip Variables

Set to `"true"` when running pipelines to skip jobs:

| Variable | Effect |
|----------|--------|
| `QUICK_TEST` | Skip all production and dev deployments |
| `SKIP_PRODUCTION_DEPLOY` | Skip production deployments only |
| `SKIP_DEV_DEPLOY` | Skip dev package deployments |
| `SKIP_NPM_PUBLISH` | Skip all npm publishing |
| `SKIP_DOCKER_PUBLISH` | Skip Docker Hub publishing |

## npm Helper Scripts

Located in `scripts/bash/npm-helpers/`:
- `npm_generate_version.sh` - Semantic versioning based on git state
- `npm_build_package.sh` - Build and package monorepos
- `npm_publish.sh` - Publish to npm registries
- `npm_pack_package.sh` - Create package tarballs

## Image Cache Lookup Scripts

Located in `scripts/bash/`:
- `find-last-source-commit.sh` - Find the most recent commit that changed source files
- `get-cached-image.sh` - Check if a Docker image exists in a registry for a commit
- `find-upstream-image.sh` - Find pre-built images from upstream repos (combines git fetch + lookup)
- `docker-image-utils.sh` - Utility functions for Docker image operations

See `docs/image-cache-lookup.md` for full documentation.

**Example: Downstream repo finding upstream image:**
```bash
# Find latest hive image without maintaining a submodule
find-upstream-image.sh \
  --repo-url=https://gitlab.syncad.com/hive/hive.git \
  --registry=registry.gitlab.syncad.com/hive/hive \
  --patterns="libraries/,programs/,CMakeLists.txt,Dockerfile"
# Outputs: UPSTREAM_IMAGE=registry.../hive:abc12345
```

## Architecture Notes

**emsdk image** (`Dockerfile.emscripten`): Contains Emscripten toolchain with Node.js, pnpm, and pre-compiled WASM dependencies (Boost, OpenSSL, secp256k1). Used by wax and other WASM projects.

**python-scripts image** (`Dockerfile.python-scripts`): Contains Python utilities for GitLab registry cleanup (`delete-image.py`, `remove-buildkit-cache.py`).

**Template inheritance**: Jobs extend from `.job-defaults` (in `base.gitlab-ci.yml`) which sets common retry policies and interruptible flags.

**Registry caching**: Docker builds use registry-based caching (`type=registry`) with automatic cleanup via `buildkit_cache_cleanup` job.

## Version Sources

- Python version: `docker-bake.hcl` (`PYTHON_VERSION`, `PYTHON_RUNTIME_VERSION`)
- Emscripten version: `docker-bake.hcl` (`EMSCRIPTEN_VERSION`)
- Docker CLI/dind version: `docker-bake.hcl` (`DOCKER_VERSION`, plus `DOCKER_COMPOSE_PLUGIN_VERSION` / `DOCKER_BUILDX_PLUGIN_VERSION`); `.gitlab-ci.yml` repeats `DOCKER_VERSION` as a CI variable, kept equal by `tests/test_tool_versions_defined_once.py`
- emsdk image tag for consumers: `templates/npm_projects.gitlab-ci.yml` (`EMSCRIPTEN_IMAGE_TAG`) and alpine tag `templates/base.gitlab-ci.yml` (`ALPINE_IMAGE_TAG`); templates cannot read bake variables, so the test checks each names the tag `docker-bake.hcl` builds or an earlier one, never a later one (see below)
- npm package pins (Hive libraries, React/Next, Vue/Nuxt, Tailwind, build tooling): the catalogs in `pnpm-config/pnpm-workspace.yaml`, referenced only as `catalog:<name>`, consumed by symlinking the file or, in monorepos, merging it with `scripts/python/pnpm-catalog-sync.py`; the emsdk and node-test-tools images preload them (`scripts/python/pnpm-catalog-manifest.py`)
- Test/lint tool versions (pytest, ruff, node, pnpm, ...): `docker-bake.hcl` only - passed as build args to python-test-tools, node-test-tools, docker-builder (built on python-test-tools) and emsdk; Dockerfiles have no defaults and templates never pin a tool (see README "CI/test tool versions")

## Moving Templates to a New Image Tag (two MRs)

Templates pin tags of images this repo builds (`EMSCRIPTEN_IMAGE_TAG`, `ALPINE_IMAGE_TAG`). Projects that include the templates from `develop` pull the pinned tag as soon as it is merged, but a new tag exists only after the develop pipeline's build job has published it. A new image tag therefore always reaches the templates in two MRs:

1. **Image MR**: changes the image and moves its tag in `docker-bake.hcl` (new content always gets a new tag, never replaces what a published tag points to). The template variable keeps the published tag.
2. **Template MR**: only moves the template variable to the new tag. Open it together with the image MR, as a Draft branched from it, with `Depends on !<image MR>` in the description. Merge it only after the image MR is merged and its develop pipeline has published the tag (`docker buildx imagetools inspect registry.gitlab.syncad.com/hive/common-ci-configuration/<image>:<tag>` finds it), rebased onto `develop`.

This applies to AIDEV workflows too: a change that bumps such a tag leaves the template variable alone, and the template move is a separate issue and MR. `tests/test_tool_versions_defined_once.py` lets a template trail the bake tag but fails when it is ahead of it, and the `check_template_image_tags` job (`scripts/python/check-template-image-tags.py`) fails every branch pipeline whose templates name a tag the registry does not have: on a template MR it passes once the tag is published (retry the job then). Details: "Moving templates to a new image tag" in `docs/common-ci-images.md`.

## CI Builder Infrastructure

Jobs run on builders `hive-builder-5` through `hive-builder-11`. Key paths:
- `/cache/` - Local SSD cache (tar files)
- `/nfs/ci-cache/` - Shared NFS cache (hive-builder-10 is the NFS server)
- `/blockchain/block_log_5m/` - Static 5M block test data (read-only, don't copy)

## NFS Locking Requirement

Alpine-based images (`docker-dind`) must include `util-linux` for NFS flock support (`docker-builder` is Ubuntu-based, util-linux is in its base). BusyBox flock fails on NFS with "Bad file descriptor".

## HAF App Testing Templates

The `templates/haf_app_testing.gitlab-ci.yml` provides composable building blocks for HAF applications. See `docs/haf-app-testing-templates.md` for full documentation.

### Available Templates

**HAF Image Lookup:**
- `.find_haf_image` - Finds pre-built HAF images, outputs HAF_UPSTREAM_*, HAF_IMAGE_NAME, HAF_COMMIT

**App Configuration:**
- `.haf_app_config_variables` - Derives APP_SYNC_CACHE_TYPE, APP_CACHE_KEY from HAF_APP_NAME

**Skip Pattern Presets:**
- `.haf_app_skip_patterns_standard` - Standard patterns (tests/, docs/, *.md, etc.)
- `.haf_app_skip_patterns_with_gui` - Standard + gui/ directory
- `.haf_app_skip_patterns_with_postgrest_only` - Standard + postgrest/ directory

**Change Detection:**
- `.haf_app_detect_changes` - Detects if only tests/docs changed, enabling skip of heavy sync jobs

**Sync Job Components** (use with `!reference` tags):
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

**Tavern Test Components:**
- `.tavern_test_variables` - pytest workers, tavern version, directories
- `.tavern_install_deps` - Check the job image provides tavern/pytest (haf-app-test-runner, python-test-tools or docker-builder; nothing is installed) and link `./venv` to its tools venv for jobs that still activate one
- `.tavern_run_tests` - Run pytest with tavern
- `.tavern_test_artifacts` - Artifacts for tavern tests

**Skip Rules:**
- `.skip_on_quick_test` - Skip job when QUICK_TEST=true
- `.skip_on_auto_skip` - Skip job when AUTO_SKIP_SYNC=true
- `.skip_on_cached_data` - Skip on either condition

### Migrating a HAF App to Use Templates

1. **Add the include** to your `.gitlab-ci.yml`:
   ```yaml
   include:
     - project: 'hive/common-ci-configuration'
       ref: develop
       file: '/templates/haf_app_testing.gitlab-ci.yml'
   ```

2. **Identify common patterns** in your CI:
   - Sync jobs that fetch HAF cache, run docker-compose, save cache
   - Test jobs that extract cache, start services, run tests
   - Change detection logic for skipping heavy jobs

3. **Replace inline scripts** with `!reference` tags:
   ```yaml
   sync:
     extends:
       - .docker_image_builder_job_template
       - .haf_app_sync_variables
     variables:
       APP_SYNC_CACHE_TYPE: "haf_myapp_sync"
       APP_CACHE_KEY: "${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}"
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

4. **Test the migration** by running the pipeline and comparing behavior.

### Migration Status

| Application | Status | Notes |
|-------------|--------|-------|
| reputation_tracker | Merged | Sync + detect_changes |
| balance_tracker | Merged | Sync + smart cache lookup |
| hafah | Not started | Different pattern (no sync job) |
| hivemind | Not started | Complex, multiple test types |
| hivesense | Not started | |
| nft_tracker | Not started | |
| haf_block_explorer | Not started | |

### Required Variables

For sync templates:
- `HAF_COMMIT` - HAF submodule commit SHA
- `APP_SYNC_CACHE_TYPE` - App-specific cache type (e.g., "haf_btracker_sync")
- `APP_CACHE_KEY` - Cache key (typically `${HAF_COMMIT}_${CI_COMMIT_SHORT_SHA}`)

For QUICK_TEST mode:
- `QUICK_TEST` - Set to "true" to enable
- `QUICK_TEST_HAF_COMMIT` - HAF commit to use for cache
