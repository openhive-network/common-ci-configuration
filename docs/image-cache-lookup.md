# Image Cache Lookup

This document describes the scripts and templates for finding pre-built Docker images, avoiding unnecessary rebuilds, and looking up images from upstream repositories.

## Overview

The image cache lookup system provides:

1. **Build avoidance** - Skip building images when source code hasn't changed
2. **Cross-repo lookup** - Find pre-built images from upstream repositories (e.g., clive finding hive images)
3. **Change detection** - Automatically detect what type of files changed and skip unnecessary jobs

## Scripts

### find-last-source-commit.sh

Finds the most recent commit that changed any of the specified source file patterns.

```bash
# Find last commit that changed C++ source files
find-last-source-commit.sh "libraries/" "programs/" "CMakeLists.txt" "Dockerfile"

# Find in a specific directory with full hash
find-last-source-commit.sh --dir=/path/to/repo --full "src/" "Dockerfile"

# Quiet mode (only outputs commit hash)
find-last-source-commit.sh --quiet "src/"
```

**Options:**
- `--dir=PATH` - Directory to search in (default: current directory)
- `--abbrev=N` - Abbreviate commit to N characters (default: 8)
- `--full` - Output full 40-character hash
- `--quiet` - Only output the commit hash

### get-cached-image.sh

Checks if a Docker image exists in a registry for a given commit.

```bash
# Check if image exists
get-cached-image.sh --commit=abc12345 --registry=registry.gitlab.syncad.com/hive/hive

# Check for a specific image variant (e.g., testnet)
get-cached-image.sh --commit=abc12345 --registry=registry.gitlab.syncad.com/hive/hive --image=testnet

# Use commit from environment variable
export HIVE_COMMIT=abc12345
get-cached-image.sh --commit-var=HIVE_COMMIT --registry=registry.gitlab.syncad.com/hive/hive

# Require image to exist (exit with error if not found)
get-cached-image.sh --commit=abc12345 --registry=... --require-hit
```

**Options:**
- `--commit=HASH` - Commit hash to look up
- `--commit-var=NAME` - Environment variable containing commit hash
- `--registry=URL` - Docker registry URL
- `--image=NAME` - Image name within registry (optional)
- `--output=FILE` - Output env file (default: image-cache.env)
- `--require-hit` - Exit with error if image not found

**Output (image-cache.env):**
```bash
CACHE_HIT=true
IMAGE_COMMIT=abc12345def67890...
IMAGE_TAG=abc12345
IMAGE_NAME=registry.gitlab.syncad.com/hive/hive:abc12345
IMAGE_REGISTRY=registry.gitlab.syncad.com/hive/hive
```

### find-upstream-image.sh

Combines git fetch and image lookup for finding images from upstream repositories.

```bash
# Find latest hive image for use by clive
find-upstream-image.sh \
  --repo-url=https://gitlab.syncad.com/hive/hive.git \
  --registry=registry.gitlab.syncad.com/hive/hive \
  --patterns="libraries/,programs/,CMakeLists.txt,Dockerfile,cmake/,.gitmodules"

# Find testnet image from specific branch
find-upstream-image.sh \
  --repo-url=https://gitlab.syncad.com/hive/hive.git \
  --registry=registry.gitlab.syncad.com/hive/hive \
  --image=testnet \
  --branch=develop \
  --patterns="libraries/,programs/"
```

**Options:**
- `--repo-url=URL` - Git URL of upstream repo
- `--registry=URL` - Docker registry URL
- `--patterns=LIST` - Comma-separated source file patterns
- `--branch=NAME` - Branch to check (default: develop)
- `--depth=N` - Git fetch depth (default: 100)
- `--image=NAME` - Image name within registry
- `--require-hit` - Exit with error if image not found

**Output (upstream-image.env):**
```bash
UPSTREAM_BRANCH=develop
UPSTREAM_CACHE_HIT=true
UPSTREAM_COMMIT=abc12345
UPSTREAM_TAG=abc12345
UPSTREAM_IMAGE=registry.gitlab.syncad.com/hive/hive:abc12345
UPSTREAM_REGISTRY=registry.gitlab.syncad.com/hive/hive
```

## CI Templates

Include the template in your `.gitlab-ci.yml`:

```yaml
include:
  - project: 'hive/common-ci-configuration'
    ref: develop
    file: '/templates/source_change_detection.gitlab-ci.yml'
```

### Change Detection

Detects what type of files changed and sets variables for conditional job execution:

```yaml
variables:
  # Customize patterns for your project
  SOURCE_CODE_PATTERNS: "^(libraries/|programs/|CMakeLists\\.txt|cmake/|Dockerfile)"
  DOCS_PATTERNS: "^(.*\\.md|doc/|\\.gitignore|CODEOWNERS)"
  CI_SCRIPT_PATTERNS: "^(\\.gitlab-ci\\.yaml|scripts/ci/)"
  TEST_PATTERNS: "^(tests/)"

detect_changes:
  extends: .detect_source_changes
```

**Output variables:**
- `DOCS_ONLY=true` - Only documentation files changed
- `TESTS_ONLY=true` - Only test files changed (no source or CI)
- `SOURCE_CHANGED=true` - Source code files changed
- `CI_CHANGED=true` - CI scripts changed

### Skip Rules

Use the provided rule templates to skip jobs conditionally:

```yaml
build:
  extends: .skip_build_on_non_source_changes
  script:
    - make build

test:
  extends: .skip_test_on_docs_only
  script:
    - make test

expensive_test:
  extends: .manual_on_feature_branches
  script:
    - make expensive-test
```

### Upstream Image Lookup

Find pre-built images from upstream repositories:

```yaml
find_hive_image:
  extends: .find_upstream_image
  variables:
    UPSTREAM_REPO_URL: "https://gitlab.syncad.com/hive/hive.git"
    UPSTREAM_REGISTRY: "registry.gitlab.syncad.com/hive/hive"
    UPSTREAM_BRANCH: "develop"
    UPSTREAM_PATTERNS: "libraries/,programs/,CMakeLists.txt,Dockerfile,cmake/,.gitmodules"
    UPSTREAM_IMAGE: "testnet"  # optional

use_hive_image:
  needs: [find_hive_image]
  script:
    - echo "Using hive image: $UPSTREAM_IMAGE"
    - docker pull "$UPSTREAM_IMAGE"
```

### Local Image Check

Check if an image already exists in your own registry:

```yaml
check_my_image:
  extends: .check_local_image
  variables:
    LOCAL_REGISTRY: "${CI_REGISTRY_IMAGE}"
    LOCAL_PATTERNS: "src/,Dockerfile,CMakeLists.txt"

build:
  needs: [check_my_image]
  script:
    - |
      if [ "$CACHE_HIT" = "true" ]; then
        echo "Image already exists: $IMAGE_NAME"
        docker pull "$IMAGE_NAME"
      else
        echo "Building new image..."
        docker build -t "$IMAGE_NAME" .
        docker push "$IMAGE_NAME"
      fi
```

## Use Cases

### 1. Building Repo (hive)

Hive uses the scripts to avoid rebuilding when only tests/docs change:

```yaml
variables:
  SOURCE_CODE_PATTERNS: "^(libraries/|programs/|CMakeLists\\.txt|cmake/|Dockerfile|docker/)"

detect_changes:
  extends: .detect_source_changes

check_image:
  extends: .check_local_image
  variables:
    LOCAL_PATTERNS: "libraries/,programs/,CMakeLists.txt,cmake/,Dockerfile,docker/"

build_hived:
  extends: .skip_build_on_non_source_changes
  needs: [detect_changes, check_image]
  script:
    - |
      if [ "$CACHE_HIT" = "true" ]; then
        echo "Reusing existing image: $IMAGE_NAME"
      else
        ./scripts/ci-helpers/build_instance.sh
      fi
```

### 2. Downstream Repo (clive)

Clive looks up the latest hive image without maintaining a submodule:

```yaml
find_hive:
  extends: .find_upstream_image
  variables:
    UPSTREAM_REPO_URL: "https://gitlab.syncad.com/hive/hive.git"
    UPSTREAM_REGISTRY: "registry.gitlab.syncad.com/hive/hive"
    UPSTREAM_PATTERNS: "libraries/,programs/,CMakeLists.txt,Dockerfile"

build_clive:
  needs: [find_hive]
  script:
    - echo "Building with hive: $UPSTREAM_IMAGE"
    - docker pull "$UPSTREAM_IMAGE"
    - ./build.sh --hive-image="$UPSTREAM_IMAGE"
```

### 3. HAF App (balance_tracker)

HAF apps can find both hive and HAF images:

```yaml
find_haf:
  extends: .find_upstream_image
  variables:
    UPSTREAM_REPO_URL: "https://gitlab.syncad.com/hive/haf.git"
    UPSTREAM_REGISTRY: "registry.gitlab.syncad.com/hive/haf"
    UPSTREAM_PATTERNS: "src/,hive/,CMakeLists.txt,Dockerfile"
    UPSTREAM_OUTPUT: "haf-image.env"

sync:
  needs: [find_haf]
  script:
    - source haf-image.env
    - echo "Using HAF: $UPSTREAM_IMAGE"
```

## Migration from Hive Scripts

If your repo currently uses hive's `get_image4submodule.sh` or similar scripts:

1. **Replace `retrieve_last_commit.sh`** with `find-last-source-commit.sh`
2. **Replace `docker_image_utils.sh`** - already exists in common-ci-configuration
3. **Replace `get_image4submodule.sh`** with a combination of:
   - `find-last-source-commit.sh` (find commit)
   - `get-cached-image.sh` (check registry)
4. **Replace submodule-based lookups** with `find-upstream-image.sh`

The new scripts are more flexible and work both for same-repo and cross-repo lookups.
