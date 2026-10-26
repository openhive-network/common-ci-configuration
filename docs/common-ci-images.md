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
- Poetry, Docker CLI + buildx (`DOCKER_VERSION`, `DOCKER_BUILDX_PLUGIN_VERSION`), PostgreSQL 18 client libraries

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
- Poetry, Docker CLI + buildx (`DOCKER_VERSION`, `DOCKER_BUILDX_PLUGIN_VERSION`), sccache, websocat, faketime

**Current version:** `ubuntu26.04-pg18-1`

**Used by:** haf and hafah pipelines (`HIVE_CI_BASE_IMAGE` / `PYTHON_IMAGE` /
builder images) and hive's fc canary job.

### docker-builder

**Base:** python-test-tools (Ubuntu 26.04, Python 3.14), plus the static docker
CLI, buildx and compose of docker:<DOCKER_VERSION>-cli (`DOCKER_VERSION`,
`DOCKER_COMPOSE_PLUGIN_VERSION`, `DOCKER_BUILDX_PLUGIN_VERSION` in
docker-bake.hcl) and its entrypoint

CI image for building Docker images using BuildKit and for jobs that drive a
DinD service (HAF-app e2e/tavern tests). Runs as non-root user `hived`
(UID 1000) with sudo access.

**Includes:** everything in python-test-tools (the venv is owned by `hived`, so
jobs can `pip install` their own test packages), bash, git, curl, sudo,
util-linux (NFS flock), JMeter + m2u, openjdk 21

It is built on python-test-tools - rather than e2e jobs running
python-test-tools with a DinD service - because every existing consumer
already runs docker-builder with the DinD service and expects Python test
tooling in it (the Alpine image carried Python 3.12 for tavern/tox): keeping
the image name keeps them working while giving them the shared tool versions.

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

**Base:** Alpine (docker:<DOCKER_VERSION>-dind, `DOCKER_VERSION` from docker-bake.hcl -
the same version as the CLI in docker-builder and haf-app-test-runner)

Docker-in-Docker service image. Used as a sidecar service for jobs that need to build/run Docker containers.

**Includes:** util-linux (for NFS flock support in cache operations)

**Note:** Exposes only port 2376 to work around GitLab Runner healthcheck issues.

### emsdk

**Base:** Debian (emscripten/emsdk)

WebAssembly build environment with Emscripten toolchain and pre-compiled dependencies.

**Includes:**
- Emscripten SDK (version configured in docker-bake.hcl)
- Node.js, pnpm and Playwright at the docker-bake.hcl tool versions; the only
  global pnpm packages are `playwright` and `@playwright/test`. TypeScript,
  rollup, typedoc and the like come from each project's own `package.json` and
  lockfile (`pnpm exec` / `pnpm run`), not from the image
- Playwright's browsers belong to one Playwright version, so the
  `playwright-toolset` catalog in `pnpm-config/pnpm-workspace.yaml` (which
  consumers install their tests from) must equal `PLAYWRIGHT_VERSION`; a bump
  changes both, enforced by `tests/test_tool_versions_defined_once.py`.
  Consumers pick up the catalog by bumping their `npm-common-config`
  submodule; one still on an older catalog cannot launch the image's browsers
- Pre-compiled WASM libraries: Boost, OpenSSL, secp256k1
- Build tools: ninja, autoconf, libtool, protobuf

**Current version:** `5.0.2-7` (`5.0.2-6` has Node 24.21.0; `5.0.2-5` and older also install TypeScript,
rollup, typedoc and protobuf tooling globally; `5.0.2-4`: Node 24, Playwright
1.59; `5.0.2-3` has Node 22)

**Ubuntu release:** the image's Ubuntu (24.04 for emsdk 5.0.2) is chosen by the
upstream emscripten/emsdk image. The twiggy/bloaty builder stage builds on that
image itself instead of a separate Ubuntu tag, because binaries built on a
newer release (26.04, newer glibc) would not load in it.

**Used by:** wax and other WASM projects for building JavaScript/TypeScript packages.

## Test Tools Images

Test and lint tool versions for hive repos are defined once, as variables in
`docker-bake.hcl` (`PYTEST_VERSION`, `RUFF_VERSION`, `NODE_VERSION`, ...).
These images are tagged `:<IMAGE_VERSION>` and `:develop` (moving) from the
default branch, and `:<commit-sha>` on every build. See the README for how
repos consume them and how to bump a tool.

### python-test-tools

**Base:** python_development (Ubuntu 26.04, Python 3.14)

**Includes:** pytest, pytest-xdist, pytest-timeout, tavern, deepdiff, jsondiff,
prettytable, ruff, mypy, pylint, yamllint, sqlfluff, tox (in `/python_venv`,
first on PATH), shellcheck, postgrest, psql (PGDG client), poetry, git, jq,
xmlstarlet. The `hived` user (UID 1000, HAF container convention) owns the venv,
so jobs in the images built on this one can `pip install` their own test
packages next to the tools.

**Used by:** `.haf_app_lint_bash`, `.haf_app_lint_sql`,
`.lint_code_with_ruff_template`, `.type_check_with_mypy_template`, this
repository's lint jobs, and as the base of docker-builder (tavern jobs).

### node-test-tools

**Base:** Ubuntu 26.04

**Includes:** node, pnpm, TypeScript, Playwright with the Chromium headless
shell (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`). The full Chromium build is
not included: headless runs (Playwright's default) use the shell, and a job
that runs headed or with `channel: 'chromium'` has to `playwright install
chromium` itself.

**Used by:** `.npm_test_template`.

## Test Runner Images

### haf-app-test-runner

**Base:** python-test-tools (Ubuntu 26.04, Python 3.14), so it carries every
python-test-tools tool (pytest, xdist, tavern, deepdiff, jsondiff, psql, ...) at
the docker-bake.hcl versions

Shared test runner for HAF applications' sync and tavern jobs. Adds JMeter + m2u
(copied from benchmark-test-runner), openjdk 21 (with the `JAVA_TOOL_OPTIONS`
m2u needs), the static Docker CLI with the compose and buildx plugins (from the
official `docker:<DOCKER_VERSION>-cli` image, like docker-builder) and psycopg2 in the
tools venv. `hived` (UID 1000) owns the venv, so jobs can `pip install` their
own test packages next to the tools.

**Tags:** `develop` (moving; the default branch republishes it whenever the
image or a tool version changes), `4.0` (`HAF_APP_TEST_RUNNER_VERSION`) and
`3.1` (`HAF_APP_TEST_RUNNER_PINNED_TAG`). `3.1` is the tag HAF apps pinned
before the rebuild; it is republished with the current image so those pins get
the tools until the repos drop them, after which the tag can be retired.

`templates/haf_app_testing.gitlab-ci.yml` sets `HAF_APP_TEST_RUNNER_IMAGE` to
the `develop` tag (`HAF_APP_TEST_RUNNER_TAG`), and `.haf_app_dind_tavern_tests`
runs in it. A repo that leaves the variable unset follows tool bumps with no
change of its own.

**Used by:** balance_tracker, reputation_tracker, haf_block_explorer.

### benchmark-test-runner

**Base:** Alpine at `ALPINE_VERSION` (docker-bake.hcl)

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
needs an older Python") is obsolete - the generator requires Python >= 3.14 and
runs in ci-base-image. The only remaining consumer is hivesense; the image
will be retired once hivesense migrates.

### python-scripts

**Base:** python_runtime (Ubuntu 26.04, Python 3.14)

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

**Base:** Alpine at `ALPINE_VERSION` (docker-bake.hcl)

Minimal Alpine image mirrored to GitLab registry. Templates select it through
`ALPINE_IMAGE` (`templates/base.gitlab-ci.yml`).

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
| haf-app-test-runner | 3.14 | inherits python-test-tools |
| python_runtime | 3.14 | minimal Ubuntu 26.04 runtime, used by clive |
| python_development | 3.14 | Ubuntu 26.04 with dev tools, used by clive |
| python (deprecated) | 3.14.2 | hivesense only |
| python-scripts | 3.14 | CI utilities (registry cleanup), on python_runtime |
| python-test-tools | 3.14 | test/lint tools, on python_development |
| docker-builder | 3.14 | inherits python-test-tools |
| benchmark-test-runner | 3.x | Alpine system Python |

## Version Management

Image versions are defined in `docker-bake.hcl`:

| Variable | Current Value | Description |
|----------|---------------|-------------|
| `CI_BASE_IMAGE_VERSION` | pypa_2_28-pg18-4 | manylinux CI base image |
| `CI_BASE_IMAGE_UBUNTU_VERSION` | ubuntu26.04-pg18-1 | Ubuntu CI base image (HAF) |
| `HAF_APP_TEST_RUNNER_VERSION` | 4.0 | HAF app test runner (also tagged `develop` and `HAF_APP_TEST_RUNNER_PINNED_TAG`) |
| `PYTHON_RUNTIME_VERSION` | 3.14-u26.04-1 | python_runtime / python_development |
| `PYTHON_VERSION` | 3.14.2-1 | python image (deprecated) |
| `EMSCRIPTEN_VERSION` | 5.0.2 | Emscripten SDK version |
| `PSQL_IMAGE_VERSION` | 14-2 | PostgreSQL client version |
| `POSTGREST_VERSION` | v12.0.2 | PostgREST version |
| `ALPINE_VERSION` | 3.21.3 | Alpine base version |
| `DOCKERFILE_IMAGE_VERSION` | 1.11 | Dockerfile frontend version |
| `PYTHON_TEST_TOOLS_IMAGE_VERSION`, `NODE_TEST_TOOLS_IMAGE_VERSION` | 1 | tools images version tags |
| `TEST_TOOLS_MOVING_TAG` | develop | tools images moving tag |
| `PYTEST_VERSION` ... `TYPESCRIPT_VERSION` | see docker-bake.hcl | test/lint tool versions |

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
# benchmark-test-runner, python-test-tools, node-test-tools
```

Note: `haf-app-test-runner` and `docker-builder` build their python-test-tools
base from the same checkout (a bake `contexts` entry), so they always carry
that commit's tool versions.

## NFS Compatibility

The following Alpine-based images include `util-linux` for proper NFS flock support:
- `docker-dind`

(`docker-builder` is Ubuntu-based now; util-linux is part of its base.)

This is required for cache-manager.sh operations on NFS-mounted cache directories. BusyBox flock (Alpine default) returns "Bad file descriptor" on NFS mounts.
