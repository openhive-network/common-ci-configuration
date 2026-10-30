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

Every test and lint tool version used by hive repos is defined **once**, as a variable in [docker-bake.hcl](docker-bake.hcl): `PYTEST_VERSION`, `PYTEST_XDIST_VERSION`, `PYTEST_TIMEOUT_VERSION`, `TAVERN_VERSION`, `DEEPDIFF_VERSION`, `JSONDIFF_VERSION`, `PRETTYTABLE_VERSION`, `RUFF_VERSION`, `MYPY_VERSION`, `PYLINT_VERSION`, `YAMLLINT_VERSION`, `SQLFLUFF_VERSION`, `SHELLCHECK_VERSION`, `TOX_VERSION`, `POSTGREST_VERSION`, `PSQL_VERSION` (PostgreSQL major: python-test-tools' client, node-test-tools-pg's server), `NODE_VERSION`, `PNPM_VERSION`, `PLAYWRIGHT_VERSION` and `TYPESCRIPT_VERSION`. Python comes from `PYTHON_RUNTIME_VERSION` (Python 3.14 on Ubuntu 26.04). The Dockerfiles receive them as build args and have no defaults of their own; templates never pin a tool.

The tools are published in these images:

| Image | Contents |
|-------|----------|
| `registry.gitlab.syncad.com/hive/common-ci-configuration/python-test-tools` | Ubuntu 26.04 + Python 3.14: pytest (+ xdist, timeout), tavern, deepdiff, jsondiff, ruff, mypy, pylint, yamllint, sqlfluff, tox, shellcheck, postgrest, psql |
| `registry.gitlab.syncad.com/hive/common-ci-configuration/node-test-tools` | Ubuntu 26.04: node, pnpm, TypeScript, Playwright with the Chromium headless shell, and a pnpm store (`/opt/pnpm-store`) preloaded with every package of the pnpm catalogs |
| `registry.gitlab.syncad.com/hive/common-ci-configuration/node-test-tools-pg` | node-test-tools plus the PostgreSQL `PSQL_VERSION` server (`initdb`, `pg_ctl`, `postgres` on PATH) |

**Testing Node projects in node-test-tools.** The two node images are meant to be the images hive Node projects test in directly, without an image of their own per project: AIDEV runs a project's suites in them (sandboxed, offline, as the caller's uid), and CI jobs run in them so `pnpm install --frozen-lockfile` downloads only what the project's lockfile does not share with the catalogs. Every uid's pnpm uses the preloaded store (`npm_config_store_dir=/opt/pnpm-store` in the environment), and the store is writable by every uid, so an install adds the packages it lacks to it. The store is the top layer of both images and the same layer in both, so a catalog change re-downloads only that layer (about 215 MB gzipped; the rest of node-test-tools is about 405 MB, PostgreSQL adds 35 MB). Suites that need a database start their own cluster in node-test-tools-pg with `initdb` and `pg_ctl` on a unix socket; see [docs/common-ci-images.md](docs/common-ci-images.md#node-test-tools-and-node-test-tools-pg) for running it as a uid without a passwd entry. Both are built by one CI job from the `node-test-tools-images` bake group and share `NODE_TEST_TOOLS_IMAGE_VERSION`.

Tags: `:develop` is the moving tag the default branch republishes; `:<PYTHON|NODE>_TEST_TOOLS_IMAGE_VERSION` is also published from the default branch, and `:<commit-sha>` from every build. `docker-builder` is built on `python-test-tools`, so jobs that drive a DinD service get the same tools; `emsdk` gets node, pnpm and Playwright from the same variables (TypeScript and other build tools come from each project's own lockfile; emsdk preloads its pnpm store with the `pnpm-config` catalogs so those installs mostly avoid the network).

**The pnpm catalog.** Consumers install Playwright and TypeScript from the catalogs in [pnpm-config/pnpm-workspace.yaml](pnpm-config/pnpm-workspace.yaml). `playwright-toolset`'s `playwright` and `@playwright/test` must equal `PLAYWRIGHT_VERSION` (the images ship only that version's browsers), so a Playwright bump changes both; `tests/test_tool_versions_defined_once.py` fails naming both values otherwise. `typescript-toolset` is compared with `TYPESCRIPT_VERSION` the same way, but is a recorded exception (`CATALOG_EXCEPTIONS`): it stays on TypeScript 6.0 because consumers' typedoc and rollup TypeScript plugins need the compiler JS API, which TypeScript 7 does not ship.

[pnpm-config/pnpm-workspace.yaml](pnpm-config/pnpm-workspace.yaml) is the single source of version pins for Hive libraries, UI frameworks and build tooling. Consumers reference them only as `catalog:<name>`; they do not write a version of their own or keep a private copy of a catalog:

| Catalog | Packages |
|---------|----------|
| `hiveio-toolset` | `@hiveio/wax` and its `wax-signers-*` packages (one build), `@hiveio/beekeeper` (a CSP-safe 1.28.9-rc0 build with the large-buffer encrypt/decrypt fix, forced over the signers' own pin by the file's `overrides:`), `@hiveio/workerbee`, `@hiveio/hb-auth`, `@hiveio/healthchecker-component` |
| `react-toolset` | `react`, `react-dom`, `@types/react`, `@types/react-dom` (19.x), `next` (16.x), `eslint-config-next` (next's version), `@tanstack/react-query` (5.x) |
| `vue-toolset` | `vue` (3.5.x), `nuxt` (4.x), `pinia`, `vuetify`, `vue-tsc` |
| `tailwind-toolset` | `tailwindcss`, `@tailwindcss/postcss`, `@tailwindcss/vite`, `@tailwindcss/cli` (4.x, one version) |
| `typescript-toolset` | `typescript` (6.0.x), `tslib`, `tsx` |
| `rollup-toolset`, `typedoc-toolset`, `proto-toolset`, `playwright-toolset`, `husky`, `terser`, `size-limit-toolset` | build and test tooling |

Each package is in one catalog only (`tests/test_pnpm_catalog_manifest.py`). The emsdk and node-test-tools images preload all of them into their pnpm store by installing the package.json `scripts/python/pnpm-catalog-manifest.py` writes from `pnpm config get catalogs --json`, and that script fails on a package listed at two versions. Third-party pins are the newest stable release that was at least two weeks old when it was picked.

The `@hiveio` packages are versioned by Hive release line, not semver. `hiveio-toolset` pins the newest `-stable.<timestamp>` builds of the current line (1.28.8), not the older 2.0.x line that npmjs tags `latest`. A Hive-library bump moves these pins to newer `-stable` builds of the current release line; the signers move with wax. The signers depend on exactly a 1.28.8 beekeeper that fails to encrypt or decrypt large buffers, so the catalog pins a newer 1.28.9-rc0 beekeeper and the file's `overrides:` entry `'@hiveio/beekeeper': 'catalog:hiveio-toolset'` resolves the signers' dependency to it, leaving consumers one beekeeper. Until wax publishes signers built on the same beekeeper, a beekeeper bump keeps that override; once it does, beekeeper moves to the build the signers depend on and the override can go. The `-stable` builds are published only in the hive group's GitLab registry, so consumers need `@hiveio:registry=https://gitlab.syncad.com/api/v4/groups/136/-/packages/npm/` in their `.npmrc` (as in [pnpm-config/.npmrc](pnpm-config/.npmrc)). The catalog's comment lists the known peer mismatches, currently healthchecker-component's tailwindcss 3 peer.

**Responding to a security advisory.** Bump the package's version in ccc's catalog and merge to `develop`. Consumers track ccc's `develop` through their `npm-common-config` / `common-ci-configuration` submodule, which CI updates (`GIT_SUBMODULE_UPDATE_FLAGS: --remote`), so their next pipeline resolves the new version. The frozen install fails with `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH`, `.pnpm_install_with_catalog_fallback` reinstalls unfrozen, and the job keeps the refreshed lockfile as the `pnpm-lock.catalog-refreshed.yaml` artifact. Each consumer then commits its submodule bump and the refreshed `pnpm-lock.yaml` (a monorepo also commits its re-merged `pnpm-workspace.yaml`); for the projects listed in [pnpm-config/catalog-consumers.txt](pnpm-config/catalog-consumers.txt) ccc opens that merge request itself, see "Re-syncing consumers" below. No consumer edits a version string.

**Installs outside the templates.** Only the template jobs fall back from the frozen install. A consumer's Dockerfile, script or own CI job that runs `pnpm install --frozen-lockfile` fails with `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH` from the moment a catalog change reaches `develop` until the consumer merges its re-sync merge request, because its submodule follows `develop`. Such an install must either tolerate the bump with the same fallback as `.pnpm_install_with_catalog_fallback` (only that error reinstalls unfrozen; any other failure stays fatal), or the project must accept that it is broken until the re-sync merge request lands. In a Dockerfile:

```dockerfile
RUN if ! pnpm install --frozen-lockfile > /tmp/pnpm-install.log 2>&1; then \
      cat /tmp/pnpm-install.log; \
      grep -q ERR_PNPM_LOCKFILE_CONFIG_MISMATCH /tmp/pnpm-install.log || exit 1; \
      echo "ccc's pnpm catalog changed since pnpm-lock.yaml was written: installing unfrozen"; \
      pnpm install --no-frozen-lockfile; \
    fi
```

**Simple repos** whose layout fits the file's `packages:` globs symlink it: `pnpm-workspace.yaml -> npm-common-config/pnpm-config/pnpm-workspace.yaml`. In a single-package repo those globs match nothing, so the package is the workspace root, which `pnpm --recursive` skips. `npm_build_package.sh` (the `.npm_build_template` build) therefore adds `--include-workspace-root` when the root's package.json has a `build` script and the workspace has no other project; such a repo needs no `include-workspace-root=true` in its `.npmrc` (a root next to workspace packages is still left out, as its `build` usually builds them). Other recursive commands a single-package repo runs itself need `--include-workspace-root` or that `.npmrc` setting.

**Monorepos** with their own `packages:`, `overrides:`, settings or local catalogs keep their own `pnpm-workspace.yaml` and merge ccc's catalogs and overrides into it with the script their submodule ships:

```bash
python3 npm-common-config/scripts/python/pnpm-catalog-sync.py            # merge into ./pnpm-workspace.yaml
python3 npm-common-config/scripts/python/pnpm-catalog-sync.py --check    # print the diff and exit 1 when out of date
```

The script writes ccc's catalogs between `# BEGIN common-ci-configuration catalogs` and `# END common-ci-configuration catalogs` at the top of the file's `catalogs:` mapping and rewrites that region on every run. ccc's `overrides:` go the same way, between `# BEGIN common-ci-configuration overrides` and `# END common-ci-configuration overrides` in the file's `overrides:` mapping (created when missing). The script removes a local override whose selector ccc also overrides, so ccc's applies. It keeps every other line outside the regions as written, including the repo's other overrides. A local catalog must not reuse a ccc catalog's name; the script refuses one that does. In CI, set `CCC_SUBMODULE_DIR` to the submodule's path relative to `SOURCE_DIR`. The npm job templates then merge before they install, so a ccc bump reaches the monorepo's jobs the same way it reaches a symlinking repo. A job extending `.pnpm_catalog_check` fails when the committed file is behind. It is allowed to fail by default because the install jobs already use the merged catalogs; set `allow_failure: false` to enforce it:

```yaml
variables:
  CCC_SUBMODULE_DIR: "npm-common-config"

pnpm_catalog_check:
  extends: .pnpm_catalog_check
  stage: build
```

**Re-syncing consumers.** [pnpm-config/catalog-consumers.txt](pnpm-config/catalog-consumers.txt) lists the projects consuming the catalogs, one `<project path> <ccc submodule path> <mode> [no-auto-merge]` per line. Mode `merge` is a monorepo whose default branch runs `.pnpm_catalog_check`; mode `symlink` is a project whose `pnpm-workspace.yaml` is a symlink into the submodule. Add a project there when it adopts the check or the symlink (find the symlinking ones by looking for a `pnpm-workspace.yaml` symlink to `<submodule>/pnpm-config/pnpm-workspace.yaml` in the `hive` group's default branches). On a push to `develop` that changes `pnpm-config/`, ccc's `pnpm_catalog_resync` job ([scripts/python/pnpm-catalog-resync.py](scripts/python/pnpm-catalog-resync.py)) clones each one's default branch and moves its ccc submodule to the new `develop` commit. In a `merge` project it runs that commit's `pnpm-catalog-sync.py` on `pnpm-workspace.yaml` and refreshes `pnpm-lock.yaml`; in a `symlink` project it refreshes the `pnpm-lock.yaml` of every directory whose `pnpm-workspace.yaml` is such a symlink (e.g. wax's `ts/` and its examples). Lockfiles are refreshed with `pnpm install --lockfile-only` in node-test-tools (the job refuses a pnpm other than `docker-bake.hcl`'s `PNPM_VERSION`). When a file changed it force-pushes the commit, which also moves the submodule, to the consumer's `ccc-catalog-resync` branch and opens, or updates, one merge request to the default branch, titled with the ccc commit and set to merge when its pipeline succeeds; when that branch already carries the same tree (a retried job) it is not pushed again, so the consumer's pipeline does not restart. The job runs in the `pnpm_catalog_resync` resource group, so two `develop` pipelines never update a consumer's branch at once. A project listed with `no-auto-merge` (hive/vessel) gets the merge request without auto-merge, for a person to review and merge. A consumer already in sync gets nothing, and an open re-sync merge request it no longer needs is closed. One consumer's failure does not stop the others: the job fails at the end naming them (it is allowed to fail, so ccc's pipeline only warns). The job can also be run by hand on a `develop` pipeline, or by a schedule setting `PNPM_CATALOG_RESYNC=true`.

A consumer whose `.aidev/project.yaml` pins a runtime image with `pnpm-lock.yaml` among its `inputs` also fails AIDEV's runtime-lockstep gate on the re-sync merge request, since the lockfile changes without the image. Such a project has to rebuild and re-pin its runtime image on that branch (or in a follow-up) itself; ccc's CI does not do it.

It authenticates with the masked, protected CI/CD variable `CATALOG_RESYNC_TOKEN`: a token with the `api` and `write_repository` scopes and a role on every listed project that may push branches and merge to its default branch (Developer, or Maintainer where only maintainers merge), e.g. a `hive` group access token. While the variable is unset the job prints that it is skipping and passes.

**Checking consumers before a catalog change lands.** On the pipeline of a branch with an open merge request that changes `pnpm-config/` (`$CI_OPEN_MERGE_REQUESTS` is set; ccc runs branch pipelines only), the `pnpm_catalog_consumer_check` job ([scripts/python/pnpm-catalog-consumer-check.py](scripts/python/pnpm-catalog-consumer-check.py)) checks every listed consumer against the branch's catalogs: it clones the default branch, points the ccc submodule at the branch's commit (merging the catalogs into a `merge` project's `pnpm-workspace.yaml`), runs `pnpm install --no-frozen-lockfile` with ccc's pnpm in each directory the re-sync would refresh, and then the type check: the directory's `typecheck`, `type-check` or `tsc` script when its package.json has one, otherwise that script or `tsc --noEmit -p tsconfig.json` in each workspace project. The log shows each consumer's install and type check output and result; the `pnpm-catalog-consumer-check.txt` artifact lists them all. A project with no type check counts as passing once it installs. The job only warns (`allow_failure`); run the branch's pipeline with `PNPM_CATALOG_CHECK_BLOCKING=true` (Build > Pipelines > Run pipeline) to make a failing consumer fail it. On a push to a branch without an open merge request the job does not start by itself: it is a manual job there. It is not a baseline comparison: a consumer whose type check already fails on its own default branch, or needs generated sources the install does not produce, fails here too, so read the log before treating a failure as caused by the catalog change. It clones with `CATALOG_RESYNC_TOKEN` (read only) when the pipeline has the variable; as a protected variable it is absent on unprotected branches, where the consumers are cloned anonymously, which works while they are public. The token is only used for the clone: each consumer's install and type check, which run its lifecycle scripts and dependency builds, get an environment without it, without the job's own credentials (`CI_JOB_TOKEN`, `CI_REPOSITORY_URL`, `CI_REGISTRY_PASSWORD`, ...) and without the clone's git auth header.

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
