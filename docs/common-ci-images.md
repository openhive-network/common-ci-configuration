# Common CI Images

Docker images built by common-ci-configuration for use across Hive blockchain CI/CD pipelines.

## Image Registry

All images are published to:
```
registry.gitlab.syncad.com/hive/common-ci-configuration/<image-name>:<tag>
```

Version tags are published only from the default branch (`develop`); every CI
build additionally pushes a commit-SHA tag for traceability. Version defaults
live in `docker-bake.hcl`.

## Build Images

### ci-base-image

**Base:** quay.io/pypa/manylinux_2_28 (AlmaLinux 8, glibc 2.28)

Primary build environment for hive/wax C++ compilation and Python testing.
Binaries built here run on any distro with glibc >= 2.28 (the manylinux
portability floor).

**Python:** all CPython versions 3.9-3.14 (+ 3.14t free-threaded) preinstalled;
default 3.14, selectable at runtime via the `PYTHON_VERSION` environment
variable (`select-python` wrapper).

**Includes:**
- C++ build toolchain (gcc-toolset, cmake 3.28.6, ninja, sccache, clang 21)
- Boost (built from source, static, -fPIC), OpenSSL, snappy, zopfli
- Prebuilt RocksDB (commit-hash-indexed) and the aarch64 buildroot cross
  toolchain (`/opt/hive/cross/aarch64`)
- Poetry, Docker CLI + buildx, PostgreSQL 18 client libraries

**Current version:** `pypa_2_28-pg18-4` (canonical tag; supersedes
`pypa_2_28-4`, `pypa_2_28-pg18-3` and `pypa_2_28-pg18-arm-1`)

**Used by:** hive and wax build/test pipelines, and all Python jobs via
`templates/python_projects.gitlab-ci.yml` (`PYTHON_IMAGE`).

### ci-base-image-ubuntu

**Base:** Ubuntu 26.04 (phusion/baseimage:resolute)

Ubuntu-based build/test environment for HAF. Exists because HAF builds hived
inside Ubuntu containers and its replay/test jobs run those binaries directly -
the glibc of builder and runtime must match (manylinux glibc 2.28 cannot run
Ubuntu-built binaries).

**Python:** 3.14 (native system python3 on 26.04 - no deadsnakes PPA)

**Includes:**
- Distro toolchain: gcc 15.2, clang/clang-tidy/lld 21, CMake 4.2, Boost 1.90
- libpqxx 7.10 (built from source), PostgreSQL 18 dev packages (pgdg)
- Poetry, Docker CLI + buildx, sccache, websocat, faketime

**Current version:** `ubuntu26.04-pg18-1`

**Used by:** haf and hafah pipelines (`HIVE_CI_BASE_IMAGE` / `PYTHON_IMAGE` /
builder images), hive's fc canary job, and as the base of
`haf-app-test-runner`.

### docker-builder

**Base:** Alpine (docker:26.1.4-cli)

CI image for building Docker images using BuildKit. Runs as non-root user with sudo access.

**Includes:** bash, git, coreutils, curl, sudo, util-linux (for NFS flock support)

**Used by:**
- `prepare_hived_image` jobs in hive/haf
- `prepare_haf_data` replay jobs
- Any job that builds Docker images via `docker buildx`

**Example:**
```yaml
build_image:
  image: registry.gitlab.syncad.com/hive/common-ci-configuration/docker-builder:latest
  services:
    - name: registry.gitlab.syncad.com/hive/common-ci-configuration/docker-dind:latest
      alias: docker
```

### docker-dind

**Base:** Alpine (docker:26.1.4-dind)

Docker-in-Docker service image. Used as a sidecar service for jobs that need to build/run Docker containers.

**Includes:** util-linux (for NFS flock support in cache operations)

**Note:** Exposes only port 2376 to work around GitLab Runner healthcheck issues.

### emsdk

**Base:** Debian (emscripten/emsdk)

WebAssembly build environment with Emscripten toolchain and pre-compiled dependencies.

**Includes:**
- Emscripten SDK (version configured in docker-bake.hcl)
- Node.js 22.x with pnpm
- Pre-compiled WASM libraries: Boost, OpenSSL, secp256k1
- Build tools: ninja, autoconf, libtool, protobuf

**Current version:** `5.0.2-3`

**Used by:** wax and other WASM projects for building JavaScript/TypeScript packages.

## Test Runner Images

### haf-app-test-runner

**Base:** ci-base-image-ubuntu (Ubuntu 26.04, Python 3.14)

Shared test runner for HAF applications. Adds JMeter + m2u (copied from
benchmark-test-runner), openjdk 21, PostgreSQL client, docker-compose-v2 and
psycopg2.

**Current version:** `3.0`

**Used by:** balance_tracker, reputation_tracker, haf_block_explorer.

### benchmark-test-runner

**Base:** Alpine 3.17

**Python:** 3.x (Alpine system Python)

JMeter-based benchmark test runner. Also the source of the JMeter/m2u tools
copied into docker-builder and haf-app-test-runner.

**Used by:** Performance testing jobs.

## Runtime Images

### python_runtime

**Base:** ubuntu/python:3.14-26.04_stable (minimal chiseled image)

**Python:** 3.14

Minimal Python 3.14 runtime environment with a bootstrap layer (dpkg, sudo,
PAM, coreutils) installed from the Ubuntu 26.04 archive at build time.

**Current version:** `3.14-u26.04-1`

**Used by:** clive as runtime base image.

### python_development

**Base:** Ubuntu 26.04 (same Dockerfile as python_runtime, `python_dev` target)

**Python:** 3.14

Python development environment with additional tools for testing and development.

**Used by:** clive as testnet base image.

### python (DEPRECATED)

**Base:** Debian (python:3.14.2-slim-bookworm)

**Python:** 3.14.2

Lightweight Python environment with poetry and git.

**Current version:** `3.14.2-1`

**Status:** deprecated. Its historical reason to exist ("api_client_generator
needs Python 3.12") is obsolete - the generator requires Python >= 3.14 and
runs in ci-base-image. The only remaining consumer is hivesense; the image
will be retired once hivesense migrates.

### python-scripts

**Base:** Debian (python:3.12.2)

Contains Python utilities for CI operations:
- `delete-image.py` - GitLab registry cleanup
- `remove-buildkit-cache.py` - BuildKit cache management

**Used by:** Registry cleanup jobs (via `templates/docker_image_jobs.gitlab-ci.yml`).

## Service Images

### psql

**Base:** Alpine (ghcr.io/alphagov/paas/psql)

PostgreSQL client for database operations in CI jobs.

**Current version:** `14-2`

**Used by:** Jobs that need to run SQL queries or manage PostgreSQL databases.

### postgrest

**Base:** Alpine (postgrest/postgrest)

PostgREST API server for exposing PostgreSQL as REST API.

**Current version:** `v12.0.2`

**Used by:** API testing and HAF API node deployments.

### nginx

**Base:** Alpine (openresty/openresty:alpine)

OpenResty (nginx + Lua) for reverse proxy and API gateway.

**Used by:** Frontend deployments and API proxying.

## Utility Images

### alpine

**Base:** Alpine 3.21.3

Minimal Alpine image mirrored to GitLab registry.

**Used by:** Simple utility jobs, base for other images.

### dockerfile

**Base:** docker/dockerfile

BuildKit frontend for advanced Dockerfile features.

**Current version:** `1.11`

### tox-test-runner (RETIRED)

**Base:** Alpine (python:3.11-alpine)

No remaining consumers (hafah's pattern tests use
`.haf_app_pattern_tests_template`, which is pytest-based). Scheduled for
removal together with its bake target and template block.

## Python Version Summary

| Image | Python Version | Notes |
|-------|----------------|-------|
| ci-base-image | 3.9-3.14 selectable, default 3.14 | manylinux; source of truth for `PYTHON_IMAGE` |
| ci-base-image-ubuntu | 3.14 (native) | Ubuntu 26.04, for HAF glibc-matched builds |
| haf-app-test-runner | 3.14 | inherits ci-base-image-ubuntu |
| python_runtime | 3.14 | minimal Ubuntu 26.04 runtime, used by clive |
| python_development | 3.14 | Ubuntu 26.04 with dev tools, used by clive |
| python (deprecated) | 3.14.2 | hivesense only |
| python-scripts | 3.12.2 | CI utilities (registry cleanup) |
| benchmark-test-runner | 3.x | Alpine system Python |

## Version Management

Image versions are defined in `docker-bake.hcl`:

| Variable | Current Value | Description |
|----------|---------------|-------------|
| `CI_BASE_IMAGE_VERSION` | pypa_2_28-pg18-4 | manylinux CI base image |
| `CI_BASE_IMAGE_UBUNTU_VERSION` | ubuntu26.04-pg18-1 | Ubuntu CI base image (HAF) |
| `HAF_APP_TEST_RUNNER_VERSION` | 3.0 | HAF app test runner |
| `PYTHON_RUNTIME_VERSION` | 3.14-u26.04-1 | python_runtime / python_development |
| `PYTHON_VERSION` | 3.14.2-1 | python image (deprecated) |
| `EMSCRIPTEN_VERSION` | 5.0.2 | Emscripten SDK version |
| `PSQL_IMAGE_VERSION` | 14-2 | PostgreSQL client version |
| `POSTGREST_VERSION` | v12.0.2 | PostgREST version |
| `ALPINE_VERSION` | 3.21.3 | Alpine base version |
| `DOCKERFILE_IMAGE_VERSION` | 1.11 | Dockerfile frontend version |

## Building Images Locally

```bash
# Build a specific target
docker buildx bake <target>

# Build with custom tag
docker buildx bake <target> --set *.tags=myregistry/myimage:mytag

# Available targets:
# docker-builder, docker-dind, ci-base-image, ci-base-image-ubuntu,
# haf-app-test-runner, emsdk, python, python_runtime, python_development,
# python-scripts, psql, postgrest, nginx, alpine, dockerfile,
# benchmark-test-runner
```

Note: `haf-app-test-runner` builds FROM the published
`ci-base-image-ubuntu:<version>` tag on the default branch; on feature
branches it uses the commit-SHA tag pushed by the same pipeline (see the
`args` block in its bake target).

## NFS Compatibility

The following Alpine-based images include `util-linux` for proper NFS flock support:
- `docker-builder`
- `docker-dind`

This is required for cache-manager.sh operations on NFS-mounted cache directories. BusyBox flock (Alpine default) returns "Bad file descriptor" on NFS mounts.
