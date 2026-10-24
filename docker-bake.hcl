variable "CI_REGISTRY_IMAGE" {}
variable "CI_COMMIT_SHA" {}
variable "CI_COMMIT_BRANCH" {
  default = ""
}
variable "CI_DEFAULT_BRANCH" {
  default = "develop"
}
variable "EMSCRIPTEN_VERSION" {
  default = "5.0.2"
}
variable "PSQL_IMAGE_VERSION" {
  default = "14-3" # After updating tag here, remeber to also update it in job 'psql_image_test'
}
variable "DOCKERFILE_IMAGE_VERSION" {
  default = "1.11"
}
# PostgREST: the mirrored postgrest image and the binary in python-test-tools
variable "POSTGREST_VERSION" {
  default = "v12.0.2"
}
variable "PYTHON_VERSION" {
  default = "3.14.2-1"
}

variable "PYTHON_RUNTIME_VERSION" {
  default = "3.14-u26.04-1"
}

variable "CI_BASE_IMAGE_VERSION" {
  # -4: canonical tag consolidating pypa_2_28-4 / pypa_2_28-pg18-3 /
  # pypa_2_28-pg18-arm-1 (includes the prebuilt aarch64 buildroot cross
  # toolchain, see aarch64-toolchain/)
  default = "pypa_2_28-pg18-5"
}

variable "CI_BASE_IMAGE_UBUNTU_VERSION" {
  default = "ubuntu26.04-pg18-2"
}

variable "HAF_APP_TEST_RUNNER_VERSION" {
  default = "4.0"
}
# Tag HAF apps pinned before the runner was built on python-test-tools; it is
# republished with the current runner so those pins get the tools too. Drop it
# once no repo sets HAF_APP_TEST_RUNNER_IMAGE to it.
variable "HAF_APP_TEST_RUNNER_PINNED_TAG" {
  default = "3.1"
}

variable "PAAS_PSQL_VERSION" {
  default = "11251948d5dd4867552f9b9836a9e02110304df5"
}

# =============================================================================
# CI/test tool versions - the single definition for every hive repo
# =============================================================================
# python-test-tools, node-test-tools, docker-builder and emsdk receive these as
# build args; templates/ never pin a tool, they run in those images. Bumping a
# tool = change the value here; the develop pipeline republishes the images
# under their moving `develop` tag. Python itself comes from PYTHON_RUNTIME_VERSION
# (python_development is the base of python-test-tools).
variable "PYTEST_VERSION" {
  default = "9.1.1"
}
variable "PYTEST_XDIST_VERSION" {
  default = "3.8.0"
}
variable "PYTEST_TIMEOUT_VERSION" {
  default = "2.4.0"
}
variable "TAVERN_VERSION" {
  default = "3.7.1"
}
variable "DEEPDIFF_VERSION" {
  default = "9.1.0"
}
variable "JSONDIFF_VERSION" {
  default = "2.2.1"
}
variable "PRETTYTABLE_VERSION" {
  default = "3.18.0"
}
variable "RUFF_VERSION" {
  default = "0.17.2"
}
variable "MYPY_VERSION" {
  default = "2.4.0"
}
variable "PYLINT_VERSION" {
  default = "4.1.2"
}
variable "YAMLLINT_VERSION" {
  default = "1.38.0"
}
variable "SQLFLUFF_VERSION" {
  default = "4.4.0"
}
variable "SHELLCHECK_VERSION" {
  default = "v0.11.0"
}
variable "TOX_VERSION" {
  default = "4.67.1"
}
# PostgreSQL client (psql) major version, from the PGDG apt repository
variable "PSQL_VERSION" {
  default = "18"
}
variable "NODE_VERSION" {
  default = "v24.21.0"
}
variable "PNPM_VERSION" {
  default = "10.0.0"
}
# >= 1.61: the first release that supports Ubuntu 26.04
variable "PLAYWRIGHT_VERSION" {
  default = "1.64.1"
}
variable "TYPESCRIPT_VERSION" {
  default = "5.9.3"
}
# Image version tags, bumped when an image changes in a way consumers should be
# able to pin; most consumers follow TEST_TOOLS_MOVING_TAG instead
variable "PYTHON_TEST_TOOLS_IMAGE_VERSION" {
  default = "1"
}
variable "NODE_TEST_TOOLS_IMAGE_VERSION" {
  default = "1"
}
variable "TEST_TOOLS_MOVING_TAG" {
  default = "develop"
}
variable "BOOST_VERSION_TAG" {
  default = null
}
variable "OPENSSL_VERSION_TAG" {
  default = null
}
variable "ALPINE_VERSION" {
  default = "3.21.3"
}
variable "tag" {
  default = "latest"
}

function "notempty" {
  params = [variable]
  result = notequal("", variable)
}

function "generate-tags" {
  params = [target, local_tag]
  result = [
    # Version tag: only on default branch or local builds to prevent feature branches
    # from overwriting production image tags (e.g. ci-base-image:pypa_2_28-2)
    (CI_COMMIT_BRANCH == "" || CI_COMMIT_BRANCH == CI_DEFAULT_BRANCH)
      ? (notempty(CI_REGISTRY_IMAGE) ? "${CI_REGISTRY_IMAGE}/${target}:${local_tag}" : "${target}:${local_tag}")
      : "",
    # Commit SHA tag: always pushed in CI for traceability
    notempty(CI_COMMIT_SHA) ? "${CI_REGISTRY_IMAGE}/${target}:${CI_COMMIT_SHA}": ""
  ]
}

# Tools images also get the moving tag, published (like the version tag) only
# from the default branch
function "generate-tools-tags" {
  params = [target, local_tag]
  result = distinct(concat(generate-tags(target, local_tag), generate-tags(target, TEST_TOOLS_MOVING_TAG)))
}

function "generate-cache-from" {
  params = [target, local_tag]
  result = [
    notempty(CI_REGISTRY_IMAGE) ? "type=registry,ref=${CI_REGISTRY_IMAGE}/${target}/cache:${local_tag}" : "${target}:${local_tag}",
  ]
}

function "generate-cache-to" {
  params = [target, local_tag]
  result = [
    notempty(CI_REGISTRY_IMAGE) ? "type=registry,mode=max,ref=${CI_REGISTRY_IMAGE}/${target}/cache:${local_tag}" : "type=inline",
  ]
}

target "benchmark-test-runner" {
  dockerfile = "Dockerfile.benchmark-test-runner"
  tags = generate-tags("benchmark-test-runner", "${tag}")
  cache-from = generate-cache-from("benchmark-test-runner", "${tag}")
  cache-to = generate-cache-to("benchmark-test-runner", "${tag}")
}

target "docker-builder" {
  dockerfile = "Dockerfile.docker-builder"
  # Built on python-test-tools so HAF-app e2e jobs (docker CLI + DinD) get the
  # same Python and tool versions as every other test job. The context builds
  # the base from this checkout, so both images always carry the same versions.
  args = {
    PYTHON_TEST_TOOLS_TAG = TEST_TOOLS_MOVING_TAG
  }
  contexts = {
    "registry.gitlab.syncad.com/hive/common-ci-configuration/python-test-tools:${TEST_TOOLS_MOVING_TAG}" = "target:python-test-tools"
  }
  tags = generate-tags("docker-builder", "${tag}")
  cache-from = generate-cache-from("docker-builder", "${tag}")
  cache-to = generate-cache-to("docker-builder", "${tag}")
}

target "docker-dind" {
  dockerfile = "Dockerfile.docker-dind"
  tags = generate-tags("docker-dind", "${tag}")
  cache-from = generate-cache-from("docker-dind", "${tag}")
  cache-to = generate-cache-to("docker-dind", "${tag}")
}

target "python-scripts" {
  dockerfile = "Dockerfile.python-scripts"
  args = {
    PYTHON_RUNTIME_VERSION = PYTHON_RUNTIME_VERSION
  }
  contexts = {
    "registry.gitlab.syncad.com/hive/common-ci-configuration/python_runtime:${PYTHON_RUNTIME_VERSION}" = "target:python_runtime"
  }
  tags = generate-tags("python-scripts", "${tag}")
  cache-from = generate-cache-from("python-scripts", "${tag}")
  cache-to = generate-cache-to("python-scripts", "${tag}")
}

target "emsdk" {
  dockerfile = "Dockerfile.emscripten"
  tags = generate-tags("emsdk", "${EMSCRIPTEN_VERSION}-5")
  cache-from = generate-cache-from("emsdk", "${EMSCRIPTEN_VERSION}")
  cache-to = generate-cache-to("emsdk", "${EMSCRIPTEN_VERSION}")
  args = {
    EMSCRIPTEN_VERSION = "${EMSCRIPTEN_VERSION}",
    BOOST_VERSION_TAG = "${BOOST_VERSION_TAG}",
    OPENSSL_VERSION_TAG = "${OPENSSL_VERSION_TAG}",
    NODE_VERSION = "${NODE_VERSION}",
    PNPM_VERSION = "${PNPM_VERSION}",
    PLAYWRIGHT_VERSION = "${PLAYWRIGHT_VERSION}",
    TYPESCRIPT_VERSION = "${TYPESCRIPT_VERSION}"
  }
}

target "psql" {
  dockerfile = "Dockerfile.psql"
  args = {
    PAAS_PSQL_VERSION = "${PAAS_PSQL_VERSION}"
  }
  tags = generate-tags("psql", "${PSQL_IMAGE_VERSION}")
  cache-from = generate-cache-from("psql", "${PSQL_IMAGE_VERSION}")
  cache-to = generate-cache-to("psql", "${PSQL_IMAGE_VERSION}")
}

target "dockerfile" {
  dockerfile = "Dockerfile.dockerfile"
  tags = generate-tags("dockerfile", "${DOCKERFILE_IMAGE_VERSION}")
  cache-from = generate-cache-from("dockerfile", "${DOCKERFILE_IMAGE_VERSION}")
  cache-to = generate-cache-to("dockerfile", "${DOCKERFILE_IMAGE_VERSION}")
}

target "nginx" {
  dockerfile = "Dockerfile.nginx"
  tags = generate-tags("nginx", "${tag}")
  cache-from = generate-cache-from("nginx", "${tag}")
  cache-to = generate-cache-to("nginx", "${tag}")
}

target "postgrest" {
  dockerfile = "Dockerfile.postgrest"
  args = {
    POSTGREST_VERSION = "${POSTGREST_VERSION}"
  }
  tags = generate-tags("postgrest", "${POSTGREST_VERSION}")
  cache-from = generate-cache-from("postgrest", "${POSTGREST_VERSION}")
  cache-to = generate-cache-to("postgrest", "${POSTGREST_VERSION}")
}

target "alpine" {
  dockerfile = "Dockerfile.alpine"
  tags = generate-tags("alpine", "${ALPINE_VERSION}")
  cache-from = generate-cache-from("alpine", "${ALPINE_VERSION}")
  cache-to = generate-cache-to("alpine", "${ALPINE_VERSION}")
}

target "python" {
  dockerfile = "Dockerfile.python"
  tags = generate-tags("python", "${PYTHON_VERSION}")
  cache-from = generate-cache-from("python", "${PYTHON_VERSION}")
  cache-to = generate-cache-to("python", "${PYTHON_VERSION}")
}

target "python_runtime" {
  dockerfile = "Dockerfile.python_runtime"
  tags = generate-tags("python_runtime", "${PYTHON_RUNTIME_VERSION}")
  cache-from = generate-cache-from("python_runtime", "${PYTHON_RUNTIME_VERSION}")
  cache-to = generate-cache-to("python_runtime", "${PYTHON_RUNTIME_VERSION}")
}

target "python_development" {
  dockerfile = "Dockerfile.python_runtime"
  target = "python_dev"
  tags = generate-tags("python_development", "${PYTHON_RUNTIME_VERSION}")
  cache-from = generate-cache-from("python_development", "${PYTHON_RUNTIME_VERSION}")
  cache-to = generate-cache-to("python_development", "${PYTHON_RUNTIME_VERSION}")
}

target "ci-base-image" {
  dockerfile = "Dockerfile.ci-base-image"
  tags = generate-tags("ci-base-image", "${CI_BASE_IMAGE_VERSION}")
  cache-from = generate-cache-from("ci-base-image", "${CI_BASE_IMAGE_VERSION}")
  cache-to = generate-cache-to("ci-base-image", "${CI_BASE_IMAGE_VERSION}")
}

target "ci-base-image-ubuntu" {
  dockerfile = "Dockerfile.ci-base-image-ubuntu"
  tags = generate-tags("ci-base-image-ubuntu", "${CI_BASE_IMAGE_UBUNTU_VERSION}")
  cache-from = generate-cache-from("ci-base-image-ubuntu", "${CI_BASE_IMAGE_UBUNTU_VERSION}")
  cache-to = generate-cache-to("ci-base-image-ubuntu", "${CI_BASE_IMAGE_UBUNTU_VERSION}")
}

target "haf-app-test-runner" {
  dockerfile = "Dockerfile.haf-app-test-runner"
  # Built on python-test-tools like docker-builder, so HAF-app tavern jobs get
  # the tool versions defined here
  args = {
    PYTHON_TEST_TOOLS_TAG = TEST_TOOLS_MOVING_TAG
  }
  contexts = {
    "registry.gitlab.syncad.com/hive/common-ci-configuration/python-test-tools:${TEST_TOOLS_MOVING_TAG}" = "target:python-test-tools"
  }
  tags = distinct(concat(
    generate-tools-tags("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_VERSION}"),
    generate-tags("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_PINNED_TAG}")
  ))
  cache-from = generate-cache-from("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_VERSION}")
  cache-to = generate-cache-to("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_VERSION}")
}

target "python-test-tools" {
  dockerfile = "Dockerfile.python-test-tools"
  # The base is built from this checkout's Dockerfile.python_runtime (its
  # version tag is only published from the default branch)
  contexts = {
    "registry.gitlab.syncad.com/hive/common-ci-configuration/python_development:${PYTHON_RUNTIME_VERSION}" = "target:python_development"
  }
  args = {
    PYTHON_RUNTIME_VERSION = PYTHON_RUNTIME_VERSION,
    PYTEST_VERSION = PYTEST_VERSION,
    PYTEST_XDIST_VERSION = PYTEST_XDIST_VERSION,
    PYTEST_TIMEOUT_VERSION = PYTEST_TIMEOUT_VERSION,
    TAVERN_VERSION = TAVERN_VERSION,
    DEEPDIFF_VERSION = DEEPDIFF_VERSION,
    JSONDIFF_VERSION = JSONDIFF_VERSION,
    PRETTYTABLE_VERSION = PRETTYTABLE_VERSION,
    RUFF_VERSION = RUFF_VERSION,
    MYPY_VERSION = MYPY_VERSION,
    PYLINT_VERSION = PYLINT_VERSION,
    YAMLLINT_VERSION = YAMLLINT_VERSION,
    SQLFLUFF_VERSION = SQLFLUFF_VERSION,
    SHELLCHECK_VERSION = SHELLCHECK_VERSION,
    TOX_VERSION = TOX_VERSION,
    POSTGREST_VERSION = POSTGREST_VERSION,
    PSQL_VERSION = PSQL_VERSION,
  }
  tags = generate-tools-tags("python-test-tools", "${PYTHON_TEST_TOOLS_IMAGE_VERSION}")
  cache-from = generate-cache-from("python-test-tools", "${PYTHON_TEST_TOOLS_IMAGE_VERSION}")
  cache-to = generate-cache-to("python-test-tools", "${PYTHON_TEST_TOOLS_IMAGE_VERSION}")
}

target "node-test-tools" {
  dockerfile = "Dockerfile.node-test-tools"
  args = {
    NODE_VERSION = NODE_VERSION,
    PNPM_VERSION = PNPM_VERSION,
    PLAYWRIGHT_VERSION = PLAYWRIGHT_VERSION,
    TYPESCRIPT_VERSION = TYPESCRIPT_VERSION,
  }
  tags = generate-tools-tags("node-test-tools", "${NODE_TEST_TOOLS_IMAGE_VERSION}")
  cache-from = generate-cache-from("node-test-tools", "${NODE_TEST_TOOLS_IMAGE_VERSION}")
  cache-to = generate-cache-to("node-test-tools", "${NODE_TEST_TOOLS_IMAGE_VERSION}")
}
