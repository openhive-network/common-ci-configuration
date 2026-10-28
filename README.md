# Common CI Configuration

This project contains the common CI templates and scripts for Hive and Hive-related projects.

## Directory structure

- misc - miscellaneous files
- scripts/bash - Bash scripts
- scripts/bash/emscripten - source for emsdk image
- scripts/python - Python scripts
- templates - GitLab CI templates

## Job templates

- [docker_image_jobs.gitlab-ci.yml](templates/docker_image_jobs.gitlab-ci.yml) - templates for managing Docker images
- [npm_projects.gitlab-ci.yml](templates/npm_projects.gitlab-ci.yml) - templates for NPM package building and publishing
- [test_jobs.gitlab-ci.yml](templates/test_jobs.gitlab-ci.yml) - templates for running tests

## CI/test tool versions

Every test and lint tool version used by hive repos is defined **once**, as a variable in [docker-bake.hcl](docker-bake.hcl): `PYTEST_VERSION`, `PYTEST_XDIST_VERSION`, `PYTEST_TIMEOUT_VERSION`, `TAVERN_VERSION`, `DEEPDIFF_VERSION`, `JSONDIFF_VERSION`, `PRETTYTABLE_VERSION`, `RUFF_VERSION`, `MYPY_VERSION`, `PYLINT_VERSION`, `YAMLLINT_VERSION`, `SQLFLUFF_VERSION`, `SHELLCHECK_VERSION`, `TOX_VERSION`, `POSTGREST_VERSION`, `PSQL_VERSION` (PostgreSQL client major), `NODE_VERSION`, `PNPM_VERSION`, `PLAYWRIGHT_VERSION` and `TYPESCRIPT_VERSION`. Python comes from `PYTHON_RUNTIME_VERSION` (Python 3.14 on Ubuntu 26.04). The Dockerfiles receive them as build args and have no defaults of their own; templates never pin a tool.

The tools are published in two images:

| Image | Contents |
|-------|----------|
| `registry.gitlab.syncad.com/hive/common-ci-configuration/python-test-tools` | Ubuntu 26.04 + Python 3.14: pytest (+ xdist, timeout), tavern, deepdiff, jsondiff, ruff, mypy, pylint, yamllint, sqlfluff, tox, shellcheck, postgrest, psql |
| `registry.gitlab.syncad.com/hive/common-ci-configuration/node-test-tools` | Ubuntu 26.04: node, pnpm, TypeScript, Playwright with the Chromium headless shell |

Tags: `:develop` is the moving tag the default branch republishes; `:<PYTHON|NODE>_TEST_TOOLS_IMAGE_VERSION` is also published from the default branch, and `:<commit-sha>` from every build. `docker-builder` is built on `python-test-tools`, so jobs that drive a DinD service get the same tools; `emsdk` gets node, pnpm and Playwright from the same variables (TypeScript and other build tools come from each project's own lockfile).

**The pnpm catalog.** Consumers install Playwright and TypeScript from the catalogs in [pnpm-config/pnpm-workspace.yaml](pnpm-config/pnpm-workspace.yaml). `playwright-toolset`'s `playwright` and `@playwright/test` must equal `PLAYWRIGHT_VERSION` (the images ship only that version's browsers), so a Playwright bump changes both; `tests/test_tool_versions_defined_once.py` fails naming both values otherwise. `typescript-toolset` is compared with `TYPESCRIPT_VERSION` the same way, but is a recorded exception (`CATALOG_EXCEPTIONS`): it stays on TypeScript 5.x (5.9, which Next.js's type check and typescript-eslint 8 need) because consumers' typedoc (0.27/0.28 supports TypeScript up to 6.0) and rollup TypeScript plugins need the compiler JS API, which TypeScript 7 does not ship.

[pnpm-config/pnpm-workspace.yaml](pnpm-config/pnpm-workspace.yaml) is the single source of version pins for Hive libraries, UI frameworks and build tooling. Consumers reference them only as `catalog:<name>`; they do not write a version of their own or keep a private copy of a catalog:

| Catalog | Packages |
|---------|----------|
| `hiveio-toolset` | `@hiveio/wax` and its `wax-signers-*` packages (one build), `@hiveio/beekeeper` (the CSP-safe build the signers depend on), `@hiveio/workerbee`, `@hiveio/hb-auth`, `@hiveio/healthchecker-component` |
| `react-toolset` | `react`, `react-dom`, `@types/react`, `@types/react-dom` (19.x), `next` (16.x), `eslint-config-next` (next's version), `@tanstack/react-query` (5.x) |
| `vue-toolset` | `vue` (3.5.x), `nuxt` (4.x), `pinia`, `vuetify`, `vue-tsc` |
| `tailwind-toolset` | `tailwindcss`, `@tailwindcss/postcss`, `@tailwindcss/vite`, `@tailwindcss/cli` (4.x, one version) |
| `typescript-toolset` | `typescript` (5.x), `tslib`, `tsx` |
| `rollup-toolset`, `typedoc-toolset`, `proto-toolset`, `playwright-toolset`, `husky`, `terser`, `size-limit-toolset` | build and test tooling |

Each package is in one catalog only (`tests/test_pnpm_catalog_manifest.py`). The emsdk image preloads all of them into its pnpm store from the list `scripts/python/pnpm-catalog-manifest.py` prints, and that script fails on a package listed at two versions. Third-party pins are the newest stable release that was at least two weeks old when it was picked.

The `@hiveio` packages are versioned by Hive release line, not semver. `hiveio-toolset` pins the newest `-stable.<timestamp>` builds of the current line (1.28.8), not the older 2.0.x line that npmjs tags `latest`. A Hive-library bump moves these pins to newer `-stable` builds of the current release line; the signers move with wax, and beekeeper moves to the build they depend on. The `-stable` builds are published only in the hive group's GitLab registry, so consumers need `@hiveio:registry=https://gitlab.syncad.com/api/v4/groups/136/-/packages/npm/` in their `.npmrc` (as in [pnpm-config/.npmrc](pnpm-config/.npmrc)). The catalog's comment lists the known peer mismatches, currently workerbee and hb-auth declaring a wax 2.0.x peer.

**Responding to a security advisory.** Bump the package's version in ccc's catalog and merge to `develop`. Consumers track ccc's `develop` through their `npm-common-config` / `common-ci-configuration` submodule, which CI updates (`GIT_SUBMODULE_UPDATE_FLAGS: --remote`), so their next pipeline resolves the new version. The frozen install fails with `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH`, `.pnpm_install_with_catalog_fallback` reinstalls unfrozen, and the job keeps the refreshed lockfile as the `pnpm-lock.catalog-refreshed.yaml` artifact. Each consumer then commits its submodule bump and the refreshed `pnpm-lock.yaml` (a monorepo also commits its re-merged `pnpm-workspace.yaml`). No consumer edits a version string.

**Simple repos** whose layout fits the file's `packages:` globs symlink it: `pnpm-workspace.yaml -> npm-common-config/pnpm-config/pnpm-workspace.yaml`.

**Monorepos** with their own `packages:`, `overrides:`, settings or local catalogs keep their own `pnpm-workspace.yaml` and merge ccc's catalogs into it with the script their submodule ships:

```bash
python3 npm-common-config/scripts/python/pnpm-catalog-sync.py            # merge into ./pnpm-workspace.yaml
python3 npm-common-config/scripts/python/pnpm-catalog-sync.py --check    # print the diff and exit 1 when out of date
```

The script writes ccc's catalogs between `# BEGIN common-ci-configuration catalogs` and `# END common-ci-configuration catalogs` at the top of the file's `catalogs:` mapping and rewrites that region on every run. It keeps every line outside the region as written. A local catalog must not reuse a ccc catalog's name; the script refuses one that does. In CI, set `CCC_SUBMODULE_DIR` to the submodule's path relative to `SOURCE_DIR`. The npm job templates then merge before they install, so a ccc bump reaches the monorepo's jobs the same way it reaches a symlinking repo. A job extending `.pnpm_catalog_check` fails when the committed file is behind. It is allowed to fail by default because the install jobs already use the merged catalogs; set `allow_failure: false` to enforce it:

```yaml
variables:
  CCC_SUBMODULE_DIR: "npm-common-config"

pnpm_catalog_check:
  extends: .pnpm_catalog_check
  stage: build
```

**Consuming them.** The templates already run their test and lint jobs in these images (`.haf_app_lint_bash`, `.haf_app_lint_sql`, the tavern templates via docker-builder, `.lint_code_with_ruff_template`, `.type_check_with_mypy_template`, `.npm_test_template`). Other jobs use the variables from `templates/base.gitlab-ci.yml`:

```yaml
my_tests:
  image: "${PYTHON_TEST_TOOLS_IMAGE}"   # or "${NODE_TEST_TOOLS_IMAGE}"
  script:
    - pytest -n auto tests/
```

Don't pin tool versions in your repo. For a deliberate exception, override the tag (`PYTHON_TEST_TOOLS_TAG` / `NODE_TEST_TOOLS_TAG`, e.g. to a commit SHA).

**Docker.** `DOCKER_VERSION` in `docker-bake.hcl` is the version of the docker CLI that docker-builder and haf-app-test-runner copy from the official `docker:<DOCKER_VERSION>-cli` image and of the `docker:<DOCKER_VERSION>-dind` image docker-dind is built on; `DOCKER_COMPOSE_PLUGIN_VERSION` and `DOCKER_BUILDX_PLUGIN_VERSION` are the plugin versions that cli image ships, and the builds fail if the copied plugins report anything else. `.gitlab-ci.yml` cannot read bake variables, so it sets a `DOCKER_VERSION` CI variable for its cli image and dind service; `tests/test_tool_versions_defined_once.py` fails if it differs from the bake value or if the version appears literally anywhere else. Bumping Docker = change the three bake variables and the CI variable together (and mirror the two new upstream images under `mirrors/docker-<DOCKER_VERSION>-cli` / `mirrors/docker-<DOCKER_VERSION>-dind`).

**Node in your Dockerfiles.** `templates/base.gitlab-ci.yml` exports `NODE_IMAGE_VERSION`: `docker-bake.hcl`'s `NODE_VERSION` without the `v`, as official `node:` image tags spell it (`tests/test_tool_versions_defined_once.py` fails if the two differ). It is not called `NODE_VERSION` because `docker buildx bake` reads CI variables, so that name would override bake's own (v-prefixed) `NODE_VERSION` in jobs that build with bake. `.docker_build_job_template` passes it to every build as `--build-arg NODE_VERSION=$NODE_IMAGE_VERSION`, so a production/runtime Dockerfile takes Node from ccc with no version of its own:

```dockerfile
ARG NODE_VERSION
FROM node:${NODE_VERSION}-alpine
```

A Dockerfile without the ARG ignores it. For a deliberate exception set `NODE_VERSION=<version>` in the job's `DOCKER_BUILD_ARGS`, which wins. Jobs that build images their own way can pass `--build-arg NODE_VERSION=$NODE_IMAGE_VERSION` themselves. Local-dev hints (`.nvmrc`, volta, `engines`) should name only the major (e.g. `24`), so they don't need a change on every patch bump.

**Bumping a tool.** Change its variable in `docker-bake.hcl` and merge to `develop`. The develop pipeline rebuilds the images that use it (python-test-tools, node-test-tools, docker-builder, emsdk, ...) and moves the `:develop` tag, so every repo picks up the new version on its next pipeline. Fix only the repos the new version actually breaks.

**Staying in step with aidev.** The versions follow aidev's toolchain definition, which aidev publishes at https://gitlab.syncad.com/hive/aidev-toolchain/-/raw/main/toolchain.yaml (aidev never reads this repository). `tests/test_aidev_toolchain_drift.py`, run by the `test_repository` job on every branch pipeline, fetches that copy anonymously (`AIDEV_TOOLCHAIN_URL` overrides the URL) and fails listing every tool, and the Python/Ubuntu platform, where `docker-bake.hcl` differs; it skips when the copy can't be fetched or parsed. Its `TOOLS` table maps aidev entries to bake variables, and a deliberate difference goes in its `EXCEPTIONS` with the reason. On drift, bump whichever side is behind.

## Pipeline Skip Variables

The following variables can be set to `"true"` when running a pipeline to skip certain jobs:

| Variable | Effect |
|----------|--------|
| `QUICK_TEST` | Skip all production deployments and dev package deployments |
| `SKIP_PRODUCTION_DEPLOY` | Skip all production deployments (npm to npmjs.org, Docker to Docker Hub) |
| `SKIP_DEV_DEPLOY` | Skip dev package deployments (npm to internal GitLab registry) |
| `SKIP_NPM_PUBLISH` | Skip all npm publishing jobs (both dev and production) |
| `SKIP_DOCKER_PUBLISH` | Skip Docker Hub publishing jobs |

These variables are checked in the template rules, so projects that extend templates without overriding rules automatically get this behavior.

## Example jobs

The GitLab CI configuration for this repository contains example jobs based on the templates defined in it. On top of that the Docker images are built by jobs also based on said templates.

The password required by the **example_image_cleanup_job** is either personal, group or project access token with permission to use GitLab API.

Example JMeter benchmark job shows how to expose the JMeter result dashboard via GitLab pages.
For the example job they are available at `https://hive.pages.syncad.com/-/common-ci-configuration/-/jobs/<job-id>/artifacts/web_jmeter_report/index.html`.

## Miscellaneous files

Currently available miscellaneous files are [checkstyle2junit.xslt](misc/checkstyle2junit.xslt), [docker-compose.dind.yml](misc/docker-compose.dind.yml) and [example-jmeter-test.jmx](misc/example-jmeter-test.jmx).

`checkstyle2junit.xslt` is an XSL transformation file, which can be used to transform checkstyle-style test reports into junit-style ones. You can see how to use it in job **lint_bash_scripts**.

`docker-compose.dind.yml` is a Compose file for setting up a simple Docker-in-Docker container. To connect to that container with a Docker CLI one use command like:

```bash
docker run -it --rm --network docker -e DOCKER_TLS_CERTDIR=/certs -v docker-certs-client:/certs/client:ro --name docker-cli docker:<DOCKER_VERSION>-cli
```

where `<DOCKER_VERSION>` is the `DOCKER_VERSION` of [docker-bake.hcl](docker-bake.hcl), the version of the DinD container.

The iportant bit is connecting to the right network (`--network docker`) and mounting the TLS certificates (`-e DOCKER_TLS_CERTDIR=/certs -v docker-certs-client:/certs/client:ro`). Other parameters can be changed to suit your purposes.

`example-jmeter-test.jmx` is an example Jmeter test file used in the exmaple Jmeter job. It shows some basic test configuration as well as how to configure creation of the XML result file for m2u.
