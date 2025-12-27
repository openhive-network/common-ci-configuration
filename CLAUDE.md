# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **common-ci-configuration** - a shared CI/CD template library for Hive blockchain projects. It provides:
- GitLab CI job templates for Docker image building, testing, and publishing
- Pre-built Docker images (emsdk, python, nginx, postgrest, psql, etc.)
- npm/pnpm package build and publish scripts
- Python utilities for GitLab registry management

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
# postgrest, alpine, python, python_runtime, python_development
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

## Architecture Notes

**emsdk image** (`Dockerfile.emscripten`): Contains Emscripten toolchain with Node.js, pnpm, and pre-compiled WASM dependencies (Boost, OpenSSL, secp256k1). Used by wax and other WASM projects.

**python-scripts image** (`Dockerfile.python-scripts`): Contains Python utilities for GitLab registry cleanup (`delete-image.py`, `remove-buildkit-cache.py`).

**Template inheritance**: Jobs extend from `.job-defaults` (in `base.gitlab-ci.yml`) which sets common retry policies and interruptible flags.

**Registry caching**: Docker builds use registry-based caching (`type=registry`) with automatic cleanup via `buildkit_cache_cleanup` job.

## Version Sources

- Python version: `docker-bake.hcl` (`PYTHON_VERSION`, `PYTHON_RUNTIME_VERSION`)
- Emscripten version: `docker-bake.hcl` (`EMSCRIPTEN_VERSION`)
- emsdk image tag for consumers: `templates/npm_projects.gitlab-ci.yml` (`EMSCRIPTEN_IMAGE_TAG`)
