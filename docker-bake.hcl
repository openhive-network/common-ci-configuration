variable "CI_REGISTRY_IMAGE" {}
variable "CI_COMMIT_SHA" {}
variable "EMSCRIPTEN_VERSION" {
  default = "3.1.43"
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
    notempty(CI_REGISTRY_IMAGE) ? "type=registry,ref=${CI_REGISTRY_IMAGE}/${target}:${local_tag}-cache" : "${target}:${local_tag}",
  ]
}

function "generate-cache-to" {
  params = [target, local_tag]
  result = [
    notempty(CI_REGISTRY_IMAGE) ? "type=registry,mode=max,ref=${CI_REGISTRY_IMAGE}/${target}:${local_tag}-cache" : "type=inline",
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

target "image-remover" {
  dockerfile = "Dockerfile.image-remover"
  tags = generate-tags("image-remover", "${tag}")
  cache-from = generate-cache-from("image-remover", "${tag}")
  cache-to = generate-cache-to("image-remover", "${tag}")
}

target "tox-test-runner" {
  dockerfile = "Dockerfile.tox-test-runner"
  tags = generate-tags("tox-test-runner", "${tag}")
  cache-from = generate-cache-from("tox-test-runner", "${tag}")
  cache-to = generate-cache-to("tox-test-runner", "${tag}")
}

target "emsdk" {
  dockerfile = "Dockerfile.emscripten"
  tags = generate-tags("emsdk", "${EMSCRIPTEN_VERSION}")
  cache-from = generate-cache-from("emsdk", "${EMSCRIPTEN_VERSION}")
  cache-to = generate-cache-to("emsdk", "${EMSCRIPTEN_VERSION}")
  args = {
    EMSCRIPTEN_VERSION = "${EMSCRIPTEN_VERSION}",
    BOOST_VERSION_TAG = "${BOOST_VERSION_TAG}",
    OPENSSL_VERSION_TAG = "${OPENSSL_VERSION_TAG}"
  }
}