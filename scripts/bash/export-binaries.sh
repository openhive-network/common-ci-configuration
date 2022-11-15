#! /bin/bash

set -euo pipefail

echo -e "\e[0Ksection_start:$(date +%s):binaries_export[collapsed=true]\r\e[0KExporting binaries..."
IMAGE_TAGGED_NAME=${1:-"Missing image name"}
EXPORT_PATH=${2:-"Missing export target directory"}

echo "Attempting to export built binaries from image: ${IMAGE_TAGGED_NAME} into directory: ${EXPORT_PATH}"

export DOCKER_BUILDKIT=1

docker build -o "${EXPORT_PATH}" - << EOF
    FROM scratch
    COPY --from=${IMAGE_TAGGED_NAME} /home/hived/bin/ /
EOF
echo -e "\e[0Ksection_end:$(date +%s):binaries_export\r\e[0K"