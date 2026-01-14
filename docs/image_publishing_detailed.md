# Docker Image Publishing - Detailed CI Job Analysis

This document provides a detailed per-job breakdown of Docker image publishing across the Hive ecosystem.

---

## Hive (Core Blockchain)

### Job: `publish_docker_image` (hive/.gitlab-ci.yaml:1070-1079)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration/templates/docker_image_jobs.gitlab-ci.yml:75-101) |
| **Trigger** | Protected tags only (`$CI_COMMIT_TAG && $CI_COMMIT_REF_PROTECTED`) |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | `scripts/ci-helpers/build_and_publish_instance.sh` (hive) |
| **Needs** | None (template has `needs: []`) |
| **Artifacts consumed** | None |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Cache strategy** | Docker BuildKit registry cache (implicit) |
| **Dockerfile** | `Dockerfile` (hive), target: `instance` |
| **Build args** | `BUILD_HIVE_TESTNET=OFF`, `HIVE_CONVERTER_BUILD=OFF`, git metadata |
| **Variables** | `CI_REGISTRY_PASSWORD`, `DOCKER_HUB_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hive | registry.gitlab.syncad.com/hive/hive | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:115 → build_instance.sh:147-162 (hive) |
| hive | hiveio/hive (Docker Hub) | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:118-128 (hive) |
| hive | registry-upload.hive.blog/hive/hive | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:118-135 (hive) |

**Refactoring notes:**
- Publishes to 3 registries; most other repos only publish to 2
- Uses explicit multi-registry logic in script rather than template

---

### Job: `mirrornet_node_build` (hive/.gitlab-ci.yaml:174-210)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.prepare_hived_image` (hive/scripts/ci-helpers/prepare_data_image_job.yml:32-47) |
| **Trigger** | All branches and tags (with skip on QUICK_TEST/DOCS_ONLY) |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/get_image4submodule.sh` (hive) |
| **Needs** | `detect_changes` (optional) |
| **Artifacts consumed** | None |
| **Skip conditions** | `QUICK_TEST`, `DOCS_ONLY`, `TESTS_ONLY` |
| **Cache strategy** | Via get_image4submodule.sh |
| **Dockerfile** | `Dockerfile` (hive), target: `instance` |
| **Build args** | `BUILD_HIVE_TESTNET=OFF`, `HIVE_CONVERTER_BUILD=ON` |
| **Variables** | `HIVED_CI_IMGBUILDER_USER`, `HIVED_CI_IMGBUILDER_PASSWORD` |

| Image | Registry | Tag(s) | Condition | Script Chain |
|-------|----------|--------|-----------|--------------|
| hive/mirrornet | registry.gitlab.syncad.com/hive/hive/mirrornet | `latest` | CI_COMMIT_BRANCH == "develop" | after_script inline:201-204 (hive/.gitlab-ci.yaml) |
| hive/mirrornet | registry.gitlab.syncad.com/hive/hive/mirrornet | `stable` | CI_COMMIT_BRANCH == "master" | after_script inline:205-208 (hive/.gitlab-ci.yaml) |
| hive/mirrornet | registry.gitlab.syncad.com/hive/hive/mirrornet | `${CI_COMMIT_TAG}` | CI_COMMIT_TAG is set | after_script inline:209-212 (hive/.gitlab-ci.yaml) |

**Refactoring notes:**
- Publishing in after_script is unusual; most repos use dedicated publish jobs
- Branch-based tagging (latest/stable) differs from other repos that use `develop` tag

---

### Job: `generate_testing_block_logs` (hive/.gitlab-ci.yaml:1508-1563)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.extended_block_log_creation`, `.test_tools_based` |
| **Trigger** | All commits (except QUICK_TEST) |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/testing_block_log_image_generator.sh` (hive) |
| **Needs** | None |
| **Artifacts consumed** | None |
| **Skip conditions** | `QUICK_TEST` |
| **Cache strategy** | Checksum-based skip (skips if image exists) |
| **Dockerfile** | Dynamically generated (nginx:alpine3.20-slim base) |
| **Build args** | None |
| **Variables** | `HIVED_CI_IMGBUILDER_USER`, `HIVED_CI_IMGBUILDER_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| testing-block-logs | registry.gitlab.syncad.com/hive/hive/testing-block-logs | `${GENERATOR_NAME}-${CHECKSUM}` (9 variants) | testing_block_log_image_generator.sh:67-71 (hive) |

**Refactoring notes:**
- Uses parallel:matrix for 9 generator variants
- Checksum-based tagging is unique to this repo

---

### Job: `build_combined_testing_block_logs_image` (hive/.gitlab-ci.yaml:1565-1588)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job` |
| **Trigger** | After generate_testing_block_logs completes |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | `scripts/ci-helpers/build_combined_testing_block_logs_image.sh` (hive) |
| **Needs** | `generate_testing_block_logs` (all 9 matrix jobs) |
| **Artifacts consumed** | Checksum env files from generate_testing_block_logs |
| **Skip conditions** | `QUICK_TEST` |
| **Cache strategy** | Checksum-based skip |
| **Dockerfile** | Dynamically generated multi-stage |
| **Build args** | None |
| **Variables** | Inherited |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| testing-block-logs | registry.gitlab.syncad.com/hive/hive/testing-block-logs | `combined-${COMBINED_CHECKSUM}` | build_combined_testing_block_logs_image.sh:74-85 (hive) |

---

### Job: `build_openapi_spec_image` (hive/.gitlab-ci.yaml:1646-1696)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job` |
| **Trigger** | All commits |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | Inline docker buildx build (hive/.gitlab-ci.yaml:1656-1693) |
| **Needs** | None |
| **Artifacts consumed** | None |
| **Skip conditions** | None |
| **Cache strategy** | Registry cache (`type=registry,ref=$CI_REGISTRY_IMAGE/openapi-spec:cache`) |
| **Dockerfile** | `libraries/plugins/apis/documentation/Dockerfile` (hive) |
| **Build args** | None |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Condition | Script Chain |
|-------|----------|--------|-----------|--------------|
| openapi-spec | registry.gitlab.syncad.com/hive/hive/openapi-spec | `${CI_COMMIT_SHORT_SHA}` | Always | inline:1676-1680 (hive/.gitlab-ci.yaml) |
| openapi-spec | registry.gitlab.syncad.com/hive/hive/openapi-spec | `${CI_COMMIT_TAG}` | Protected version tags (^[01v].*) | inline:1682-1686 (hive/.gitlab-ci.yaml) |
| openapi-spec | registry.hive.blog/hive/openapi-spec | `${CI_COMMIT_TAG}` | Protected version tags | inline:1688-1693 (hive/.gitlab-ci.yaml) |

**Refactoring notes:**
- Uses inline script rather than external script file
- Conditional multi-registry publishing based on tag pattern

---

## HAF (Hive Application Framework)

### Job: `haf_image_build` (haf/.gitlab-ci.yml:439-446)

| Field | Value |
|-------|-------|
| **Stage** | build_and_test_phase_1 |
| **Extends** | `.haf_image_build` (haf/.gitlab-ci.yml:365-437) |
| **Trigger** | All pipelines (with QUICK_TEST/AUTO_SKIP_BUILD skip) |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `get_image4submodule.sh` (fetched from hive repo at runtime) |
| **Needs** | `detect_changes` (optional) |
| **Artifacts consumed** | None |
| **Skip conditions** | `QUICK_TEST`, `AUTO_SKIP_BUILD` |
| **Cache strategy** | BuildKit + two-tier HAF data cache (local + NFS) |
| **Dockerfile** | `Dockerfile` (haf), target: `instance` |
| **Build args** | `BUILD_HIVE_TESTNET=OFF`, `HIVE_CONVERTER_BUILD=OFF`, git metadata |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haf | registry.gitlab.syncad.com/hive/haf | `${CI_COMMIT_SHORT_SHA}` (8 chars) | get_image4submodule.sh → build_instance.sh (hive submodule) |

---

### Job: `haf_image_build_testnet` (haf/.gitlab-ci.yml:448-455)

| Field | Value |
|-------|-------|
| **Stage** | build_and_test_phase_1 |
| **Extends** | `.haf_image_build` |
| **Trigger** | All pipelines |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | Same as haf_image_build |
| **Build args** | `BUILD_HIVE_TESTNET=ON`, `HIVE_CONVERTER_BUILD=OFF` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haf/testnet | registry.gitlab.syncad.com/hive/haf/testnet | `${CI_COMMIT_SHORT_SHA}` | get_image4submodule.sh (hive) |

---

### Job: `haf_image_build_mirrornet` (haf/.gitlab-ci.yml:457-484)

| Field | Value |
|-------|-------|
| **Stage** | build_and_test_phase_1 |
| **Extends** | `.haf_image_build` |
| **Trigger** | All pipelines + branch-specific publishing in after_script |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | Same as haf_image_build |
| **Build args** | `BUILD_HIVE_TESTNET=OFF`, `HIVE_CONVERTER_BUILD=ON` |

| Image | Registry | Tag(s) | Condition | Script Chain |
|-------|----------|--------|-----------|--------------|
| haf/mirrornet | registry.gitlab.syncad.com/hive/haf/mirrornet | `${CI_COMMIT_SHORT_SHA}` | Always | get_image4submodule.sh (hive) |
| haf/mirrornet | registry.gitlab.syncad.com/hive/haf/mirrornet | `latest` | CI_COMMIT_BRANCH == "develop" | after_script:472-475 (haf/.gitlab-ci.yml) |
| haf/mirrornet | registry.gitlab.syncad.com/hive/haf/mirrornet | `stable` | CI_COMMIT_BRANCH == "master" | after_script:476-479 (haf/.gitlab-ci.yml) |
| haf/mirrornet | registry.gitlab.syncad.com/hive/haf/mirrornet | `${CI_COMMIT_TAG}` | CI_COMMIT_TAG is set | after_script:480-484 (haf/.gitlab-ci.yml) |

---

### Job: `build_and_publish_image` (haf/.gitlab-ci.yml:1317-1333)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration/templates/docker_image_jobs.gitlab-ci.yml:75-101) |
| **Trigger** | Protected tags only |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | Fetches `build_and_publish_instance.sh` from hive repo (HIVE_SCRIPTS_REF) |
| **Needs** | None (`needs: []` from template) |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Variables** | `CI_REGISTRY_PASSWORD`, `DOCKER_HUB_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haf | registry.gitlab.syncad.com/hive/haf | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:133 (hive) |
| haf | hiveio/haf (Docker Hub) | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:134 (hive) |
| haf | registry-upload.hive.blog/haf | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:135 (hive) |

---

## Hivemind

### Job: `prepare_hivemind_image` (hivemind/.gitlab-ci.yml:413-430)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job_template` (common-ci-configuration) |
| **Trigger** | All branches |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | `scripts/ci-helpers/build_instance.sh` (hivemind) |
| **Needs** | `prepare_base_images` |
| **Skip conditions** | None |
| **Cache strategy** | Docker buildx with registry cache |
| **Dockerfile** | `Dockerfile` (hivemind) + `Dockerfile.rewriter` (hivemind) |
| **Build args** | Git metadata (BUILD_TIME, GIT_COMMIT_SHA, etc.) |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hivemind | registry.gitlab.syncad.com/hive/hivemind | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:129-140 (hivemind) |
| hivemind/minimal | registry.gitlab.syncad.com/hive/hivemind/minimal | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:129-140 (hivemind) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/hivemind/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:143-153 (hivemind) |

---

### Job: `build_and_publish_image` (hivemind/.gitlab-ci.yml:1032-1041)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration) |
| **Trigger** | Git tag pipelines |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | `scripts/ci-helpers/build_and_publish_instance.sh` (hivemind) |
| **Needs** | Template default |
| **Skip conditions** | Template defaults |
| **Dockerfile** | `Dockerfile` (hivemind) + `Dockerfile.rewriter` (hivemind) |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hivemind | registry.gitlab.syncad.com/hive/hivemind | `${CI_COMMIT_TAG}` (sanitized) | build_and_publish_instance.sh → common-ci → build_instance.sh (hivemind) |
| hivemind/minimal | registry.gitlab.syncad.com/hive/hivemind/minimal | `${CI_COMMIT_TAG}` | build_instance.sh (hivemind) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/hivemind/postgrest-rewriter | `${CI_COMMIT_TAG}` | build_instance.sh (hivemind) |

---

## HAfAH (Account History)

### Job: `prepare_postgrest_hafah_image` (HAfAH/.gitlab-ci.yml:456-460)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.prepare_hafah_image` (HAfAH/.gitlab-ci.yml:406-454) |
| **Trigger** | All pipelines |
| **Runner tags** | public-runner-docker, hived |
| **Script entry** | `scripts/ci-helpers/build_instance.sh` (HAfAH) |
| **Needs** | Template default |
| **Skip conditions** | None |
| **Cache strategy** | Docker buildx |
| **Dockerfile** | `Dockerfile` (HAfAH) + `Dockerfile.rewriter` (HAfAH) |
| **Build args** | HTTP_PORT, POSTGRES_URL, git metadata |
| **Variables** | `HAFAH_CI_IMG_BUILDER_USER`, `HAFAH_CI_IMG_BUILDER_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hafah | registry.gitlab.syncad.com/hive/hafah | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:115-128 (HAfAH) |
| hafah/minimal | registry.gitlab.syncad.com/hive/hafah/minimal | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh (HAfAH) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/hafah/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:132-139 (HAfAH) |

---

### Job: `build_and_publish_image` (HAfAH/.gitlab-ci.yml:837-853)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration) |
| **Trigger** | Protected tags only |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | `scripts/ci-helpers/build_and_publish_instance.sh` (HAfAH) |
| **Needs** | None |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hafah | registry.gitlab.syncad.com/hive/hafah | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh → common-ci (HAfAH) |
| hafah | registry-upload.hive.blog/hafah | `${CI_COMMIT_TAG}` | common-ci script (HAfAH) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/hafah/postgrest-rewriter | `${CI_COMMIT_TAG}` | build_instance.sh (HAfAH) |
| postgrest-rewriter | registry-upload.hive.blog/hafah/postgrest-rewriter | `${CI_COMMIT_TAG}` | common-ci script (HAfAH) |

---

## Reputation Tracker

### Job: `docker-setup-docker-image-build` (reputation_tracker/.gitlab-ci.yml:364-372)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-build-template` (reputation_tracker/.gitlab-ci.yml:319-351) |
| **Trigger** | All branches |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/build_docker_image.sh` (reputation_tracker) |
| **Needs** | None |
| **Skip conditions** | Image existence check |
| **Cache strategy** | BuildKit registry cache (`registry.gitlab.syncad.com/hive/reputation_tracker/cache:14-1`) |
| **Dockerfile** | `Dockerfile` (reputation_tracker), target: `full-ci` |
| **Build args** | Git metadata |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| reputation_tracker | registry.gitlab.syncad.com/hive/reputation_tracker | `${CI_COMMIT_SHORT_SHA}`, `latest` (develop) | build_docker_image.sh:117 → docker buildx bake full-ci (reputation_tracker/docker-bake.hcl:86-107) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/reputation_tracker/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}` | build_docker_image.sh:126-138 (reputation_tracker) |

---

### Job: `build_and_publish_image` (reputation_tracker/.gitlab-ci.yml:720-729)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration) |
| **Trigger** | Protected tags only |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/build_and_publish_instance.sh` (reputation_tracker) |
| **Needs** | None |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD`, `DOCKER_HUB_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| reputation_tracker | registry.gitlab.syncad.com/hive/reputation_tracker | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh → common-ci → build_instance.sh (reputation_tracker) |
| reputation_tracker/instance | registry.gitlab.syncad.com/hive/reputation_tracker/instance | `${CI_COMMIT_TAG}` | build_instance.sh:65 (reputation_tracker) |
| reputation_tracker/minimal-instance | registry.gitlab.syncad.com/hive/reputation_tracker/minimal-instance | `${CI_COMMIT_TAG}` | build_instance.sh:66 (reputation_tracker) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/reputation_tracker/postgrest-rewriter | `${CI_COMMIT_TAG}` | build_docker_image.sh:135 (reputation_tracker) |
| postgrest-rewriter | registry-upload.hive.blog/reputation_tracker/postgrest-rewriter | `${CI_COMMIT_TAG}` | common-ci script |

**Refactoring notes:**
- Creates `instance` and `minimal-instance` aliases (docker tag) - consider if needed
- Hive Blog only gets postgrest-rewriter, not main image

---

## Balance Tracker

### Job: `docker-setup-docker-image-build` (balance_tracker/.gitlab-ci.yml:364-372)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-build-template` (balance_tracker/.gitlab-ci.yml:319-351) |
| **Trigger** | All branches |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/build_docker_image.sh` (balance_tracker) |
| **Needs** | None |
| **Skip conditions** | Image existence check (balance_tracker/.gitlab-ci.yml:333-344) |
| **Cache strategy** | BuildKit registry cache (`registry.gitlab.syncad.com/hive/balance_tracker/cache:14-1`) |
| **Dockerfile** | `Dockerfile` (balance_tracker), target: `full-ci` |
| **Build args** | Git metadata |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| balance_tracker | registry.gitlab.syncad.com/hive/balance_tracker | `${CI_COMMIT_SHORT_SHA}`, `latest` (develop only), `${CI_COMMIT_TAG}` (tags only) | build_docker_image.sh:79 → docker buildx bake full-ci (balance_tracker/docker-bake.hcl:86-107) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/balance_tracker/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:119-138 (balance_tracker) |

---

### Job: `build_and_publish_image` (balance_tracker/.gitlab-ci.yml:853-860)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration/templates/docker_image_jobs.gitlab-ci.yml:75-128) |
| **Trigger** | Protected tags only |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/build_and_publish_instance.sh` (balance_tracker) |
| **Needs** | Inherited from template (test jobs must pass) |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Cache strategy** | Same BuildKit registry cache as build job |
| **Dockerfile** | `Dockerfile` (balance_tracker) + `Dockerfile.rewriter` (balance_tracker) |
| **Build args** | Git metadata |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD`, `DOCKER_HUB_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| balance_tracker | registry.gitlab.syncad.com/hive/balance_tracker | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh:1-31 (balance_tracker) → common-ci → build_instance.sh (balance_tracker) |
| balance_tracker/instance | registry.gitlab.syncad.com/hive/balance_tracker/instance | `${CI_COMMIT_TAG}` | build_instance.sh:140-141 (balance_tracker) |
| balance_tracker/minimal-instance | registry.gitlab.syncad.com/hive/balance_tracker/minimal-instance | `${CI_COMMIT_TAG}` | build_instance.sh:142-143 (balance_tracker) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/balance_tracker/postgrest-rewriter | `${CI_COMMIT_TAG}` | build_instance.sh:119-138 (balance_tracker) |
| balance_tracker | registry-upload.hive.blog/balance_tracker | `${CI_COMMIT_TAG}` | common-ci script - conditional on BLOG_REGISTRY_PASSWORD |
| postgrest-rewriter | registry-upload.hive.blog/balance_tracker/postgrest-rewriter | `${CI_COMMIT_TAG}` | common-ci script - conditional |
| balance_tracker | hiveio/balance_tracker (Docker Hub) | `${CI_COMMIT_TAG}` | common-ci script - conditional on DOCKER_HUB_PASSWORD |

**Refactoring notes:**
- `instance` and `minimal-instance` are aliases - consider deprecating
- Hive Blog and Docker Hub publishing conditional on credentials

---

## HAF Block Explorer

### Job: `docker-setup-docker-image-build` (haf_block_explorer/.gitlab-ci.yml:253-265)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-base-build-template` → `.docker_image_builder_job_template` |
| **Trigger** | Not on docs-only changes |
| **Runner tags** | public-runner-docker, bigjob |
| **Script entry** | `scripts/ci-helpers/build_docker_image.sh` (haf_block_explorer) |
| **Needs** | None |
| **Skip conditions** | None |
| **Cache strategy** | BuildKit registry cache (`cache:14-1`) |
| **Dockerfile** | `Dockerfile` (haf_block_explorer), target: `full-ci` |
| **Build args** | Git metadata, PSQL_CLIENT_VERSION |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haf_block_explorer | registry.gitlab.syncad.com/hive/haf_block_explorer | `${CI_COMMIT_SHORT_SHA}`, `latest` (default branch), `${CI_COMMIT_TAG}` | build_instance.sh:107 → docker buildx bake full-ci (haf_block_explorer/docker-bake.hcl) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/haf_block_explorer/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh:123-134 (haf_block_explorer) |

---

### Job: `build_and_publish_image` (haf_block_explorer/.gitlab-ci.yml:713-727)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration) |
| **Trigger** | Protected tags only |
| **Runner tags** | public-runner-docker, standard |
| **Script entry** | `scripts/ci-helpers/build_and_publish_instance.sh` (haf_block_explorer - symlink to haf submodule) |
| **Needs** | None |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haf_block_explorer | registry.gitlab.syncad.com/hive/haf_block_explorer | `${CI_COMMIT_TAG}` | build_and_publish_instance.sh → build_instance.sh (haf_block_explorer) |
| haf_block_explorer/instance | registry.gitlab.syncad.com/hive/haf_block_explorer/instance | `${CI_COMMIT_TAG}` | build_instance.sh:145 (haf_block_explorer) |
| haf_block_explorer/minimal-instance | registry.gitlab.syncad.com/hive/haf_block_explorer/minimal-instance | `${CI_COMMIT_TAG}` | build_instance.sh:146 (haf_block_explorer) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/haf_block_explorer/postgrest-rewriter | `${CI_COMMIT_TAG}` | build_instance.sh:136 (haf_block_explorer) |
| postgrest-rewriter | registry-upload.hive.blog/haf_block_explorer/postgrest-rewriter | `${CI_COMMIT_TAG}` | inline script:721-723 (haf_block_explorer/.gitlab-ci.yml) |

**Refactoring notes:**
- Only postgrest-rewriter pushed to hive.blog (inline script), main image not pushed
- Uses symlink to haf submodule for build script

---

## HAF API Node

### Job: `docker-build` (haf_api_node/.gitlab-ci.yml:98-126)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job_template` (common-ci-configuration) |
| **Trigger** | All pushes |
| **Runner tags** | public-runner-docker |
| **Script entry** | `docker buildx bake --file=docker-bake.hcl "pipeline-images"` (inline:122) |
| **Needs** | None |
| **Skip conditions** | None |
| **Cache strategy** | Registry-based per-image (defined in docker-bake.hcl) |
| **Dockerfile** | Multiple (one per service directory) |
| **Build args** | Git metadata, TAG=${CI_COMMIT_SHORT_SHA} |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haproxy | registry.gitlab.syncad.com/hive/haf_api_node/haproxy | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake haproxy-ci (haf_api_node/docker-bake.hcl:167-172) |
| haproxy-healthchecks | registry.gitlab.syncad.com/hive/haf_api_node/haproxy-healthchecks | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake haproxy-healthchecks-ci (docker-bake.hcl:174-179) |
| caddy | registry.gitlab.syncad.com/hive/haf_api_node/caddy | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake caddy-ci (docker-bake.hcl:205-210) |
| postgrest | registry.gitlab.syncad.com/hive/haf_api_node/postgrest | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake postgrest-ci (docker-bake.hcl:236-241) |
| swagger | registry.gitlab.syncad.com/hive/haf_api_node/swagger | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake swagger-ci (docker-bake.hcl:267-272) |
| version-display | registry.gitlab.syncad.com/hive/haf_api_node/version-display | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake version-display-ci (docker-bake.hcl:306-311) |
| pgbouncer | registry.gitlab.syncad.com/hive/haf_api_node/pgbouncer | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake pgbouncer-ci (docker-bake.hcl:337-342) |
| status | registry.gitlab.syncad.com/hive/haf_api_node/status | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake status-ci (docker-bake.hcl:376-381) |
| compose | registry.gitlab.syncad.com/hive/haf_api_node/compose | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake compose-ci (docker-bake.hcl:107-112) |
| dind | registry.gitlab.syncad.com/hive/haf_api_node/dind | `${CI_COMMIT_SHORT_SHA}` | docker buildx bake dind-ci (docker-bake.hcl:114-119) |

---

### Job: `publish-images-develop` (haf_api_node/.gitlab-ci.yml:223-252)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.docker_image_builder_job_template` |
| **Trigger** | CI_COMMIT_BRANCH == "develop" |
| **Runner tags** | public-runner-docker |
| **Script entry** | `docker buildx bake "develop-images"` (inline:242) |
| **Skip conditions** | Triggered pipelines, parent pipelines |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haproxy | registry.gitlab.syncad.com/hive/haf_api_node/haproxy | `develop` | docker buildx bake haproxy-develop (docker-bake.hcl:473-481) |
| haproxy-healthchecks | registry.gitlab.syncad.com/hive/haf_api_node/haproxy-healthchecks | `develop` | docker-bake.hcl:483-491 |
| caddy | registry.gitlab.syncad.com/hive/haf_api_node/caddy | `develop` | docker-bake.hcl:493-501 |
| postgrest | registry.gitlab.syncad.com/hive/haf_api_node/postgrest | `develop` | docker-bake.hcl:503-511 |
| swagger | registry.gitlab.syncad.com/hive/haf_api_node/swagger | `develop` | docker-bake.hcl:513-521 |
| version-display | registry.gitlab.syncad.com/hive/haf_api_node/version-display | `develop` | docker-bake.hcl:523-531 |
| pgbouncer | registry.gitlab.syncad.com/hive/haf_api_node/pgbouncer | `develop` | docker-bake.hcl:533-541 |
| status | registry.gitlab.syncad.com/hive/haf_api_node/status | `develop` | docker-bake.hcl:543-551 |

---

### Job: `publish-images-release` (haf_api_node/.gitlab-ci.yml:185-221)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.docker_image_builder_job_template` |
| **Trigger** | CI_COMMIT_TAG matches `^[v01].*` |
| **Runner tags** | public-runner-docker |
| **Script entry** | `docker buildx bake "release-images"` (inline:207) |
| **Skip conditions** | Triggered pipelines, parent pipelines |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| haproxy | registry.gitlab.syncad.com/hive/haf_api_node/haproxy | `${CI_COMMIT_TAG}` | docker-bake.hcl:384-393 |
| haproxy | registry-upload.hive.blog/haf_api_node/haproxy | `${CI_COMMIT_TAG}` | docker-bake.hcl:384-393 (dual-tag) |
| (all 8 service images) | Both registries | `${CI_COMMIT_TAG}` | docker-bake.hcl release targets |

**Refactoring notes:**
- Uses docker-bake.hcl target groups for batch publishing
- CI images (compose, dind) not published to hive.blog
- Most comprehensive use of docker-bake in ecosystem

---

## NFT Tracker

### Job: `build_images` (nft_tracker/.gitlab-ci.yml:34-73)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job_template` (common-ci-configuration) |
| **Trigger** | All branches and tags |
| **Runner tags** | standard, public-runner-docker |
| **Script entry** | `scripts/ci-helpers/build_instance.sh` (nft_tracker) |
| **Needs** | None |
| **Skip conditions** | None |
| **Cache strategy** | BuildKit registry cache (`cache:14-1`) |
| **Dockerfile** | `Dockerfile` (nft_tracker) + `Dockerfile.rewriter` (nft_tracker) |
| **Build args** | Git metadata |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| nft_tracker | registry.gitlab.syncad.com/hive/nft_tracker | `${CI_COMMIT_SHORT_SHA}`, `develop` (develop branch) | build_instance.sh:111 → docker buildx bake full-ci (nft_tracker/docker-bake.hcl) |
| nft_tracker/instance | registry.gitlab.syncad.com/hive/nft_tracker/instance | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh (alias) |
| nft_tracker/minimal-instance | registry.gitlab.syncad.com/hive/nft_tracker/minimal-instance | `${CI_COMMIT_SHORT_SHA}` | build_instance.sh (alias) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/nft_tracker/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}`, `develop` (develop branch) | inline:52-61 (nft_tracker/.gitlab-ci.yml) |

---

### Job: `publish_release_images` (nft_tracker/.gitlab-ci.yml:75-129)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration) |
| **Trigger** | Protected version tags (v* or numeric) |
| **Runner tags** | standard, public-runner-docker |
| **Script entry** | `scripts/ci-helpers/build_instance.sh` (nft_tracker) |
| **Needs** | None |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| nft_tracker | registry.gitlab.syncad.com/hive/nft_tracker | `${CI_COMMIT_TAG}`, `latest` | build_instance.sh (nft_tracker) |
| nft_tracker | registry-upload.hive.blog/nft_tracker | `${CI_COMMIT_TAG}`, `latest` | inline:105,113 (nft_tracker/.gitlab-ci.yml) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/nft_tracker/postgrest-rewriter | `${CI_COMMIT_TAG}`, `latest` | inline:91-102 (nft_tracker/.gitlab-ci.yml) |
| postgrest-rewriter | registry-upload.hive.blog/nft_tracker/postgrest-rewriter | `${CI_COMMIT_TAG}`, `latest` | inline:106,114 (nft_tracker/.gitlab-ci.yml) |

---

## HiveSense

### Job: `build_images` (hivesense/.gitlab-ci.yml:228-264)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job_template` (common-ci-configuration) |
| **Trigger** | All branches and tags |
| **Runner tags** | public-runner-docker |
| **Script entry** | `scripts/build_images.sh --push` (hivesense) |
| **Needs** | None |
| **Skip conditions** | None |
| **Cache strategy** | Docker BuildKit with `develop` tag as cache source |
| **Dockerfile** | Multiple: `Dockerfile`, `Dockerfile.rewriter`, `Dockerfile.syncer`, `Dockerfile.pca` (hivesense) |
| **Build args** | BUILDKIT_INLINE_CACHE=1 |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hivesense | registry.gitlab.syncad.com/hive/hivesense | `${CI_COMMIT_SHORT_SHA}`, `develop` (develop branch) | build_images.sh:64-69,98 (hivesense) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/hivesense/postgrest-rewriter | `${CI_COMMIT_SHORT_SHA}`, `develop` | build_images.sh:71-77,99 (hivesense) |
| syncer | registry.gitlab.syncad.com/hive/hivesense/syncer | `${CI_COMMIT_SHORT_SHA}`, `develop` | build_images.sh:79-85,100 (hivesense) |
| pca | registry.gitlab.syncad.com/hive/hivesense/pca | `${CI_COMMIT_SHORT_SHA}`, `develop` | build_images.sh:87-93,101 (hivesense) |

---

### Job: `publish_release_images` (hivesense/.gitlab-ci.yml:266-297)

| Field | Value |
|-------|-------|
| **Stage** | publish |
| **Extends** | `.publish_docker_image_template` (common-ci-configuration) |
| **Trigger** | Protected tags only |
| **Runner tags** | public-runner-docker |
| **Script entry** | `scripts/build_images.sh --tag="$CI_COMMIT_TAG" --push` (hivesense) |
| **Needs** | `build_images` |
| **Skip conditions** | `QUICK_TEST`, `SKIP_PRODUCTION_DEPLOY`, `SKIP_DOCKER_PUBLISH` |
| **Variables** | `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| hivesense | registry.gitlab.syncad.com/hive/hivesense | `${CI_COMMIT_TAG}` | build_images.sh (hivesense) |
| hivesense | registry-upload.hive.blog/hivesense | `${CI_COMMIT_TAG}` | inline:284,290 (hivesense/.gitlab-ci.yml) |
| postgrest-rewriter | registry.gitlab.syncad.com/hive/hivesense/postgrest-rewriter | `${CI_COMMIT_TAG}` | build_images.sh (hivesense) |
| postgrest-rewriter | registry-upload.hive.blog/hivesense/postgrest-rewriter | `${CI_COMMIT_TAG}` | inline:285,291 (hivesense/.gitlab-ci.yml) |
| syncer | registry.gitlab.syncad.com/hive/hivesense/syncer | `${CI_COMMIT_TAG}` | build_images.sh (hivesense) |
| syncer | registry-upload.hive.blog/hivesense/syncer | `${CI_COMMIT_TAG}` | inline:286,292 (hivesense/.gitlab-ci.yml) |
| pca | registry.gitlab.syncad.com/hive/hivesense/pca | `${CI_COMMIT_TAG}` | build_images.sh (hivesense) |
| pca | registry-upload.hive.blog/hivesense/pca | `${CI_COMMIT_TAG}` | inline:287,293 (hivesense/.gitlab-ci.yml) |

**Refactoring notes:**
- Uses custom `build_images.sh` script rather than common-ci wrapper
- Does not use docker-bake.hcl (plain docker build)
- 4 images vs typical 2 for HAF apps

---

## Block Explorer UI

### Job: `docker-build` (block_explorer_ui/.gitlab-ci.yml:22-143)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_image_builder_job_template` (common-ci-configuration) |
| **Trigger** | All branches and tags |
| **Runner tags** | public-runner-docker |
| **Script entry** | `scripts/build_instance.sh` (block_explorer_ui) |
| **Needs** | None |
| **Skip conditions** | None |
| **Cache strategy** | BuildKit registry cache (`cache:${TAG}`) |
| **Dockerfile** | `Dockerfile` (block_explorer_ui) |
| **Build args** | BASE_PATH, git metadata |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_USER`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Condition | Script Chain |
|-------|----------|--------|-----------|--------------|
| block_explorer_ui | registry.gitlab.syncad.com/hive/block_explorer_ui | `${CI_COMMIT_SHORT_SHA}` | Always | build_instance.sh:71 → docker buildx bake ci-build (block_explorer_ui/docker-bake.hcl) |
| block_explorer_ui | registry.gitlab.syncad.com/hive/block_explorer_ui | `${CI_COMMIT_TAG}` | Protected tags | inline:50 |
| block_explorer_ui | registry.gitlab.syncad.com/hive/block_explorer_ui | `latest` | Default branch (master) | inline:54 |
| block_explorer_ui | registry.gitlab.syncad.com/hive/block_explorer_ui | `develop` | Develop branch | inline:58 |
| block_explorer_ui | registry-upload.hive.blog/block_explorer_ui | `${CI_COMMIT_SHORT_SHA}`, `${CI_COMMIT_TAG}` | Protected tags (^[v01]) | inline:89-96 |
| explorer-subdirectory | registry.gitlab.syncad.com/hive/block_explorer_ui/explorer-subdirectory | Same tags as above | All | build_instance.sh:108 with --base-path="/explorer" |
| explorer-subdirectory | registry-upload.hive.blog/block_explorer_ui/explorer-subdirectory | Same tags | Protected tags | inline:126-133 |

**Refactoring notes:**
- Builds 2 variants (root + /explorer subdirectory) in same job
- Complex inline tag logic - could be simplified with docker-bake groups
- No separate publish job - build job does everything

---

## Denser

### Job: `docker-build-blog` (denser/.gitlab-ci.yml:230-235)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_build_template` (denser/.gitlab-ci.yml:122-151) |
| **Trigger** | All branches and tags |
| **Runner tags** | public-runner-docker |
| **Script entry** | `scripts/ci-helpers/docker-build-template-script.sh` (denser) |
| **Needs** | None |
| **Skip conditions** | None |
| **Cache strategy** | BuildKit registry cache per app (`${TURBO_APP_NAME}/cache:${TAG}`) |
| **Dockerfile** | `Dockerfile` (denser) - Turborepo monorepo |
| **Build args** | TURBO_APP_SCOPE=@hive/blog, TURBO_APP_PATH=/apps/blog, TURBO_APP_NAME=blog, git metadata |
| **Variables** | `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD`, `BLOG_REGISTRY_USER`, `BLOG_REGISTRY_PASSWORD` |

| Image | Registry | Tag(s) | Condition | Script Chain |
|-------|----------|--------|-----------|--------------|
| blog | registry.gitlab.syncad.com/hive/denser/blog | `${CI_COMMIT_SHA}`, `${TAG}` | Always | docker-build-template-script.sh:32 → build_instance.sh:145 → docker buildx bake ci-build (denser/docker-bake.hcl) |
| blog | registry.gitlab.syncad.com/hive/denser/blog | `latest` | Main branch | docker-build-template-script.sh:10-12 |
| blog | registry.gitlab.syncad.com/hive/denser/blog | `develop` | Develop branch | docker-build-template-script.sh:14-16 |
| blog | registry-upload.hive.blog/denser/blog | `${CI_COMMIT_TAG}` | Protected tags (^[v01].*) | docker-build-template-script.sh (conditional) |

---

### Job: `docker-build-wallet` (denser/.gitlab-ci.yml:237-242)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_build_template` |
| **Build args** | TURBO_APP_SCOPE=@hive/wallet, TURBO_APP_PATH=/apps/wallet, TURBO_APP_NAME=wallet |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| wallet | registry.gitlab.syncad.com/hive/denser/wallet | Same tagging as blog | Same script chain |
| wallet | registry-upload.hive.blog/denser/wallet | `${CI_COMMIT_TAG}` | Conditional on protected tags |

---

### Job: `docker-build-blog-subdirectory` (denser/.gitlab-ci.yml:244-254)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_build_template` |
| **Trigger** | Auto on tags, manual on branches |
| **Build args** | TURBO_APP_NAME=blog-subdirectory, BASE_PATH=/blog |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| blog-subdirectory | registry.gitlab.syncad.com/hive/denser/blog-subdirectory | Same tagging | Same script chain |

---

### Job: `docker-build-wallet-subdirectory` (denser/.gitlab-ci.yml:256-266)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker_build_template` |
| **Trigger** | Auto on tags, manual on branches |
| **Build args** | TURBO_APP_NAME=wallet-subdirectory, BASE_PATH=/wallet |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| wallet-subdirectory | registry.gitlab.syncad.com/hive/denser/wallet-subdirectory | Same tagging | Same script chain |

---

### Job: `docker-build-blog-testenv` (denser/.gitlab-ci.yml:268-274)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-build-testenv-image` (denser/.gitlab-ci.yml:200-228) |
| **Trigger** | All branches |
| **Needs** | `docker-build-blog` |
| **Dockerfile** | Inline (FROM $BLOG_IMAGE_NAME, COPY .env.testing) |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| blog-testenv | registry.gitlab.syncad.com/hive/denser/blog-testenv | `${CI_COMMIT_SHA}` | inline:214-219 (denser/.gitlab-ci.yml) |

---

### Job: `docker-build-wallet-testenv` (denser/.gitlab-ci.yml:276-282)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-build-testenv-image` |
| **Needs** | `docker-build-wallet` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| wallet-testenv | registry.gitlab.syncad.com/hive/denser/wallet-testenv | `${CI_COMMIT_SHA}` | inline (denser/.gitlab-ci.yml) |

---

### Job: `docker-build-blog-mirrornet-testenv` (denser/.gitlab-ci.yml:284-292)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-build-testenv-image` |
| **Needs** | `docker-build-blog` |
| **Special** | Uses `.env.mirrornet-testing` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| blog-mirrornet-testenv | registry.gitlab.syncad.com/hive/denser/blog-mirrornet-testenv | `${CI_COMMIT_SHA}` | inline (denser/.gitlab-ci.yml) |

---

### Job: `docker-build-wallet-mirrornet-testenv` (denser/.gitlab-ci.yml:294-302)

| Field | Value |
|-------|-------|
| **Stage** | build |
| **Extends** | `.docker-build-testenv-image` |
| **Needs** | `docker-build-wallet` |
| **Special** | Uses `.env.mirrornet-testing` |

| Image | Registry | Tag(s) | Script Chain |
|-------|----------|--------|--------------|
| wallet-mirrornet-testenv | registry.gitlab.syncad.com/hive/denser/wallet-mirrornet-testenv | `${CI_COMMIT_SHA}` | inline (denser/.gitlab-ci.yml) |

**Refactoring notes:**
- 8 separate build jobs for variants - could potentially consolidate with matrix
- Testenv images use inline Dockerfile - simple but not reusable
- Uses Turborepo for monorepo build - unique in ecosystem
- No dedicated publish job - all publishing in build jobs

---

## Cross-Repository Inconsistencies Summary

### Script Naming

| Pattern | Repos Using |
|---------|-------------|
| `build_instance.sh` | hive, haf, hivemind, HAfAH, balance_tracker, haf_block_explorer, nft_tracker, block_explorer_ui |
| `build_images.sh` | hivesense |
| `build_docker_image.sh` (wrapper) | reputation_tracker, balance_tracker, haf_block_explorer |
| `docker-build-template-script.sh` | denser |
| No external script (docker-bake only) | haf_api_node |

### Tagging Strategy

| Develop Branch Tag | Repos |
|--------------------|-------|
| `develop` | nft_tracker, hivesense, block_explorer_ui, denser, haf_api_node |
| `latest` | hive (mirrornet), haf (mirrornet), balance_tracker |
| None | hivemind, HAfAH, reputation_tracker |

| Master/Main Branch Tag | Repos |
|------------------------|-------|
| `stable` | hive (mirrornet), haf (mirrornet) |
| `latest` | block_explorer_ui, denser |
| None | Most others |

### Registry Publishing

| Registries | Repos |
|------------|-------|
| GitLab + Docker Hub + Hive Blog | hive, haf |
| GitLab + Hive Blog | hivemind, HAfAH, balance_tracker, haf_block_explorer, haf_api_node, nft_tracker, hivesense, block_explorer_ui, denser |
| GitLab only | reputation_tracker (partial - only rewriter to hive.blog) |

### docker-bake.hcl Usage

| Status | Repos |
|--------|-------|
| Full adoption | haf_api_node, balance_tracker, reputation_tracker, haf_block_explorer, nft_tracker, block_explorer_ui, denser |
| Not used | hive, haf, hivemind, HAfAH, hivesense |

### Image Aliases

| Alias Pattern | Repos |
|---------------|-------|
| `/instance`, `/minimal-instance` | balance_tracker, haf_block_explorer, nft_tracker |
| `/minimal` | hivemind, HAfAH |
| None | Others |

---

## Standardized Publishing Migration Status

All repos have been migrated to use `.docker_publish_job_template` from common-ci-configuration. This template implements a pull/retag/push pattern where:

1. **Build phase**: Images built and pushed to GitLab registry only
2. **Publish phase**: Images pulled from GitLab, retagged, and pushed to hive.blog registry
3. **No rebuild**: Publish phase does not rebuild images, ensuring tested images are deployed

### Migration Status

| Repository | Status | Job Name | Images Published |
|------------|--------|----------|------------------|
| hive | Migrated | `publish_images` | hive |
| haf | Migrated | `publish_images` | haf |
| hivemind | Migrated | `publish_images` | hivemind, postgrest-rewriter |
| HAfAH | Migrated | `publish_images` | hafah, postgrest-rewriter |
| balance_tracker | Migrated | `publish_images` | balance_tracker, postgrest-rewriter |
| reputation_tracker | Migrated | `publish_images` | reputation_tracker, postgrest-rewriter |
| haf_block_explorer | Migrated | `publish_images` | haf_block_explorer, postgrest-rewriter |
| nft_tracker | Migrated | `publish_images` | nft_tracker, postgrest-rewriter |
| hivesense | Migrated | `publish_images` | hivesense, postgrest-rewriter, syncer, pca |
| haf_api_node | Migrated | `publish_images` | Multiple service images |
| block_explorer_ui | Migrated | `publish_images` | block_explorer_ui, explorer-subdirectory |
| denser | Migrated | `publish_images` | blog, wallet |

### Template Usage

```yaml
# Standard usage pattern
publish_images:
  extends: .docker_publish_job_template
  stage: publish
  variables:
    PUBLISH_IMAGES: "image1 image2"  # Space-separated list of subimages
  tags:
    - public-runner-docker
```

The template automatically:
- Runs only on protected tags
- Logs into both GitLab and hive.blog registries
- Pulls images by `CI_COMMIT_TAG` from GitLab
- Retags and pushes to `registry-upload.hive.blog`
- Respects `SKIP_DOCKER_PUBLISH` and `QUICK_TEST` variables
