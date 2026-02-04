#!/bin/bash
set -e

# Generate HTML documentation using TypeDoc
#
# This script generates TypeScript API documentation as HTML files.
#
# Usage: generate_html_docs.sh --repo-url <url> --revision <sha> --output-dir <dir> \
#                              --package-version <version> --package-name <name> \
#                              --entry-point <file> [--pnpm-dir <dir>]
#
# Options:
#   --repo-url         Repository URL for source links (e.g., "https://github.com/openhive-network/wax")
#   --revision         Git revision for source links
#   --output-dir       Directory for generated HTML docs
#   --package-version  Package version to display in docs
#   --package-name     Package name to display in docs (e.g., "@hiveio/wax")
#   --entry-point      TypeScript entry point file (e.g., "wasm/lib/index.ts")
#   --pnpm-dir         Optional: Directory to run pnpm from (for monorepos)
#   --readme           Optional: Path to README file (default: README.md)
#   --tsconfig         Optional: Path to tsconfig.json (default: tsconfig.json)

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
PROJECT_DIR="${SCRIPTPATH}/../.."

# Parse arguments
REPO_URL=""
REVISION_INFO=""
OUTPUT_DIR=""
PACKAGE_VERSION=""
PACKAGE_NAME=""
ENTRY_POINT=""
PNPM_DIR=""
README_PATH="README.md"
TSCONFIG_PATH="tsconfig.json"

while [[ $# -gt 0 ]]; do
  case $1 in
    --repo-url)
      REPO_URL="$2"
      shift 2
      ;;
    --revision)
      REVISION_INFO="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --package-version)
      PACKAGE_VERSION="$2"
      shift 2
      ;;
    --package-name)
      PACKAGE_NAME="$2"
      shift 2
      ;;
    --entry-point)
      ENTRY_POINT="$2"
      shift 2
      ;;
    --pnpm-dir)
      PNPM_DIR="$2"
      shift 2
      ;;
    --readme)
      README_PATH="$2"
      shift 2
      ;;
    --tsconfig)
      TSCONFIG_PATH="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Validate required arguments
: "${REPO_URL:?Missing --repo-url argument}"
: "${REVISION_INFO:?Missing --revision argument}"
: "${OUTPUT_DIR:?Missing --output-dir argument}"
: "${PACKAGE_VERSION:?Missing --package-version argument}"
: "${PACKAGE_NAME:?Missing --package-name argument}"
: "${ENTRY_POINT:?Missing --entry-point argument}"

mkdir -vp "${OUTPUT_DIR}"

# Build pnpm command
PNPM_CMD="pnpm"
if [ -n "${PNPM_DIR}" ]; then
  PNPM_CMD="pnpm --dir ${PNPM_DIR}"
fi

# Generate HTML documentation using TypeDoc
${PNPM_CMD} exec typedoc \
  --sourceLinkTemplate "${REPO_URL}/blob/{gitRevision}/{path}#L{line}" \
  --gitRevision "${REVISION_INFO}" \
  --readme "${README_PATH}" \
  --tsconfig "${TSCONFIG_PATH}" \
  --name "${PACKAGE_NAME} - v${PACKAGE_VERSION}" \
  --out "${OUTPUT_DIR}" \
  "${ENTRY_POINT}"

echo "=== HTML documentation generated at: ${OUTPUT_DIR} ==="
