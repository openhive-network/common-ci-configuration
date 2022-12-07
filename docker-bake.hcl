variable "CI_REGISTRY_IMAGE" {}
variable "CI_COMMIT_SHA" {}
variable "tag" {
  default = "latest"
}

function "notempty" {
  params = [variable]
  result = notequal("", variable)
}

function "generate-tags" {
  params = [target]
  result = [
    notempty(CI_REGISTRY_IMAGE) ? "${CI_REGISTRY_IMAGE}/${target}:${tag}" : "${target}:${tag}",
    notempty(CI_COMMIT_SHA) ? "${CI_REGISTRY_IMAGE}/${target}:${CI_COMMIT_SHA}": ""
  ]
}

function "generate-cache-from" {
  params = [target]
  result = [
    notempty(CI_REGISTRY_IMAGE) ? "type=registry,ref=${CI_REGISTRY_IMAGE}/${target}:${tag}-cache" : "${target}:${tag}",
  ]
}

function "generate-cache-to" {
  params = [target]
  result = [
    notempty(CI_REGISTRY_IMAGE) ? "type=registry,mode=max,ref=${CI_REGISTRY_IMAGE}/${target}:${tag}-cache" : "type=inline",
  ]
}

target "benchmark-test-runner" {
  dockerfile = "Dockerfile.benchmark-test-runner"
  tags = generate-tags("benchmark-test-runner")
  cache-from = generate-cache-from("benchmark-test-runner")
  cache-to = generate-cache-to("benchmark-test-runner")
}

target "docker-builder" {
  dockerfile = "Dockerfile.docker-builder"
  tags = generate-tags("docker-builder")
  cache-from = generate-cache-from("docker-builder")
  cache-to = generate-cache-to("docker-builder")
}

target "docker-dind" {
  dockerfile = "Dockerfile.docker-dind"
  tags = generate-tags("docker-dind")
  cache-from = generate-cache-from("docker-dind")
  cache-to = generate-cache-to("docker-dind")
}

target "image-remover" {
  dockerfile = "Dockerfile.image-remover"
  tags = generate-tags("image-remover")
  cache-from = generate-cache-from("image-remover")
  cache-to = generate-cache-to("image-remover")
}

target "tox-test-runner" {
  dockerfile = "Dockerfile.tox-test-runner"
  tags = generate-tags("tox-test-runner")
  cache-from = generate-cache-from("tox-test-runner")
  cache-to = generate-cache-to("tox-test-runner")
}