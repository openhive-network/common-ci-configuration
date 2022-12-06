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

target "benchmark-test-runner" {
  dockerfile = "Dockerfile.benchmark-test-runner"
  tags = generate-tags("benchmark-test-runner")
}

target "docker-builder" {
  dockerfile = "Dockerfile.docker-builder"
  tags = generate-tags("docker-builder")
}

target "docker-dind" {
  dockerfile = "Dockerfile.docker-dind"
  tags = generate-tags("docker-dind")
}

target "image-remover" {
  dockerfile = "Dockerfile.image-remover"
  tags = generate-tags("image-remover")
}

target "tox-test-runner" {
  dockerfile = "Dockerfile.tox-test-runner"
  tags = generate-tags("tox-test-runner")
}