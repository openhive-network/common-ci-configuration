variable "CI_REGISTRY_IMAGE" {}
variable "CI_COMMIT_SHA" {}
variable "EMSCRIPTEN_VERSION" {
  default = "3.1.56"
}
variable "PSQL_IMAGE_VERSION" {
  default = "14-1" # After updating tag here, remeber to also update it in job 'psql_image_test'
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
  tags = generate-tags("emsdk", "${EMSCRIPTEN_VERSION}-6")
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