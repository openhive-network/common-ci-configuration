# Common CI Images

Docker images built by common-ci-configuration for use across Hive blockchain CI/CD pipelines.

## Image Registry

All images are published to:
```
registry.gitlab.syncad.com/hive/common-ci-configuration/<image-name>:<tag>
```

## Build Images

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

### ci-base-image

**Base:** Ubuntu 24.04 (phusion/baseimage)

Full build environment for hive/HAF C++ compilation and Python testing.

**Python:** 3.14

**Includes:**
- C++ build toolchain (cmake, ninja, ccache)
- Python 3.14 with poetry
- Docker CLI and buildx
- PostgreSQL client libraries (libpq-dev)
- Compression libraries (zstd, snappy)

**Current version:** `ubuntu24.04-py3.14-2`

**Used by:** hive and HAF build/test pipelines that need the full toolchain.

### emsdk

**Base:** Debian (emscripten/emsdk)

WebAssembly build environment with Emscripten toolchain and pre-compiled dependencies.

**Includes:**
- Emscripten SDK (version configured in docker-bake.hcl)
- Node.js 22.x with pnpm
- Pre-compiled WASM libraries: Boost, OpenSSL, secp256k1
- Build tools: ninja, autoconf, libtool, protobuf

**Current version:** `4.0.18-1`

**Used by:** wax and other WASM projects for building JavaScript/TypeScript packages.

## Runtime Images

### python

**Base:** Debian (python:3.12.9-slim-bookworm)

**Python:** 3.12.9

Lightweight Python environment with poetry and git for running Python applications and CI jobs.

**Includes:** poetry, git

**Current version:** `3.12.9-1`

**Used by:** Python-based services, test runners, and API generation jobs that require Python 3.12 (e.g., api_client_generator which doesn't support Python 3.14 yet).

### python_runtime

**Base:** Ubuntu 24.04

**Python:** 3.12

Minimal Python 3.12 runtime environment.

**Current version:** `3.12-u24.04-1`

### python_development

**Base:** Ubuntu 24.04 (same Dockerfile as python_runtime, different target)

**Python:** 3.12

Python development environment with additional tools for testing and development.

### python-scripts

**Base:** Debian (python:3.12.2)

**Python:** 3.12.2

Contains Python utilities for CI operations:
- `delete-image.py` - GitLab registry cleanup
- `remove-buildkit-cache.py` - BuildKit cache management

**Used by:** Registry cleanup jobs.

## Service Images

### psql

**Base:** Alpine (ghcr.io/alphagov/paas/psql)

PostgreSQL client for database operations in CI jobs.

**Current version:** `14-1`

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

### benchmark-test-runner

**Base:** Alpine 3.17

**Python:** 3.x (Alpine system Python)

JMeter-based benchmark test runner.

**Used by:** Performance testing jobs.

### tox-test-runner

**Base:** Alpine (python:3.11-alpine)

**Python:** 3.11

Python tox test runner for multi-version Python testing.

**Used by:** Python package testing across multiple Python versions.

## Image Usage Status

### Images Used in CI Templates

| Image | Used in Templates | Purpose |
|-------|-------------------|---------|
| `python-scripts` | Yes | Registry cleanup utilities |
| `docker-builder` | Yes | Building Docker images |
| `docker-dind` | Yes | Docker-in-Docker service |
| `emsdk` | Yes | WASM builds |
| `tox-test-runner` | Yes | Python multi-version testing |
| `benchmark-test-runner` | Yes | JMeter performance tests |
| `alpine` | Yes | Base image for various jobs |
| `nginx` | Yes | Reverse proxy / API gateway |
| `postgrest` | Yes | REST API for PostgreSQL |
| `psql` | Yes | PostgreSQL client |
| `ci-base-image` | No | Used directly by hive/haf pipelines |
| `python` | No | Used by hive for api_client_generator (needs Python 3.12) |
| `python_runtime` | No | Used by clive as runtime base image |
| `python_development` | No | Used by clive as testnet base image |
| `dockerfile` | No | BuildKit frontend |

## Python Version Summary

| Image | Python Version | Notes |
|-------|----------------|-------|
| ci-base-image | 3.14 | Latest Python for hive/HAF testing |
| python | 3.12.9 | With poetry+git, used by hive for api_client_generator |
| python_runtime | 3.12 | Minimal Ubuntu runtime, used by clive |
| python_development | 3.12 | Ubuntu with dev tools, used by clive |
| python-scripts | 3.12.2 | CI utilities |
| tox-test-runner | 3.11 | Multi-version testing |
| benchmark-test-runner | 3.x | Alpine system Python |

## Version Management

Image versions are defined in `docker-bake.hcl`:

| Variable | Current Value | Description |
|----------|---------------|-------------|
| `EMSCRIPTEN_VERSION` | 4.0.18 | Emscripten SDK version |
| `PYTHON_VERSION` | 3.12.9-1 | Python image version (with poetry) |
| `PYTHON_RUNTIME_VERSION` | 3.12-u24.04-1 | Python runtime version |
| `CI_BASE_IMAGE_VERSION` | ubuntu24.04-py3.14-2 | CI base image version |
| `PSQL_IMAGE_VERSION` | 14-1 | PostgreSQL client version |
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
# docker-builder, docker-dind, ci-base-image, emsdk, python, python_runtime,
# python_development, python-scripts, psql, postgrest, nginx, alpine,
# dockerfile, benchmark-test-runner, tox-test-runner
```

## NFS Compatibility

The following Alpine-based images include `util-linux` for proper NFS flock support:
- `docker-builder`
- `docker-dind`

This is required for cache-manager.sh operations on NFS-mounted cache directories. BusyBox flock (Alpine default) returns "Bad file descriptor" on NFS mounts.
