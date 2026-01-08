variable "CI_REGISTRY_IMAGE" {}
variable "CI_COMMIT_SHA" {}
variable "EMSCRIPTEN_VERSION" {
  default = "4.0.22"
}
variable "PSQL_IMAGE_VERSION" {
  default = "14-1" # After updating tag here, remeber to also update it in job 'psql_image_test'
}
variable "DOCKERFILE_IMAGE_VERSION" {
  default = "1.11"
}
variable "POSTGREST_VERSION" {
  default = "v12.0.2"
}
variable "PYTHON_VERSION" {
  default = "3.12.9-1"
}

variable "PYTHON_RUNTIME_VERSION" {
  default = "3.12-u24.04-1"
}

variable "CI_BASE_IMAGE_VERSION" {
  default = "ubuntu24.04-py3.14-2"
}

variable "HAF_APP_TEST_RUNNER_VERSION" {
  default = "2.1"
}

variable "PAAS_PSQL_VERSION" {
  default = "11251948d5dd4867552f9b9836a9e02110304df5"
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
    notempty(CI_REGISTRY_IMAGE) ? "${CI_REGISTRY_IMAGE}/${target}:${local_tag}" : "${target}:${local_tag}",
    notempty(CI_COMMIT_SHA) ? "${CI_REGISTRY_IMAGE}/${target}:${CI_COMMIT_SHA}": ""
  ]
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
  tags = generate-tags("python-scripts", "${tag}")
  cache-from = generate-cache-from("python-scripts", "${tag}")
  cache-to = generate-cache-to("python-scripts", "${tag}")
}

target "tox-test-runner" {
  dockerfile = "Dockerfile.tox-test-runner"
  tags = generate-tags("tox-test-runner", "${tag}")
  cache-from = generate-cache-from("tox-test-runner", "${tag}")
  cache-to = generate-cache-to("tox-test-runner", "${tag}")
}

target "emsdk" {
  dockerfile = "Dockerfile.emscripten"
  tags = generate-tags("emsdk", "${EMSCRIPTEN_VERSION}-1")
  cache-from = generate-cache-from("emsdk", "${EMSCRIPTEN_VERSION}")
  cache-to = generate-cache-to("emsdk", "${EMSCRIPTEN_VERSION}")
  args = {
    EMSCRIPTEN_VERSION = "${EMSCRIPTEN_VERSION}",
    BOOST_VERSION_TAG = "${BOOST_VERSION_TAG}",
    OPENSSL_VERSION_TAG = "${OPENSSL_VERSION_TAG}"
  }
}

target "psql" {
  dockerfile = "Dockerfile.psql"
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
  cache-from = generate-cache-from("python_rutime", "${PYTHON_RUNTIME_VERSION}")
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

target "haf-app-test-runner" {
  dockerfile = "Dockerfile.haf-app-test-runner"
  tags = generate-tags("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_VERSION}")
  cache-from = generate-cache-from("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_VERSION}")
  cache-to = generate-cache-to("haf-app-test-runner", "${HAF_APP_TEST_RUNNER_VERSION}")
}
