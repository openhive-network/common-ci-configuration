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

Tags: `:develop` is the moving tag the default branch republishes; `:<PYTHON|NODE>_TEST_TOOLS_IMAGE_VERSION` is also published from the default branch, and `:<commit-sha>` from every build. `docker-builder` is built on `python-test-tools`, so jobs that drive a DinD service get the same tools; `emsdk` gets node, pnpm, Playwright and TypeScript from the same variables.

**Consuming them.** The templates already run their test and lint jobs in these images (`.haf_app_lint_bash`, `.haf_app_lint_sql`, the tavern templates via docker-builder, `.lint_code_with_ruff_template`, `.type_check_with_mypy_template`, `.npm_test_template`). Other jobs use the variables from `templates/base.gitlab-ci.yml`:

```yaml
my_tests:
  image: "${PYTHON_TEST_TOOLS_IMAGE}"   # or "${NODE_TEST_TOOLS_IMAGE}"
  script:
    - pytest -n auto tests/
```

Don't pin tool versions in your repo. For a deliberate exception, override the tag (`PYTHON_TEST_TOOLS_TAG` / `NODE_TEST_TOOLS_TAG`, e.g. to a commit SHA).

**Bumping a tool.** Change its variable in `docker-bake.hcl` and merge to `develop`. The develop pipeline rebuilds the images that use it (python-test-tools, node-test-tools, docker-builder, emsdk, ...) and moves the `:develop` tag, so every repo picks up the new version on its next pipeline. Fix only the repos the new version actually breaks.

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
docker run -it --rm --network docker -e DOCKER_TLS_CERTDIR=/certs -v docker-certs-client:/certs/client:ro --name docker-cli docker:20.10.21
```

The iportant bit is connecting to the right network (`--network docker`) and mounting the TLS certificates (`-e DOCKER_TLS_CERTDIR=/certs -v docker-certs-client:/certs/client:ro`). Other parameters can be changed to suit your purposes.

`example-jmeter-test.jmx` is an example Jmeter test file used in the exmaple Jmeter job. It shows some basic test configuration as well as how to configure creation of the XML result file for m2u.
