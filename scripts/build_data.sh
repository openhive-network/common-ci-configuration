#!/bin/bash
# build_data.sh - Prepare blockchain data for CI testing
#
# This script prepares a data directory with block_log and runs a replay
# to generate the blockchain state needed for testing.
#
# Usage: build_data.sh <image> [OPTIONS]
#
# Required:
#   <image>                         Docker image to use for replay
#
# Options:
#   --data-cache=PATH               Directory for data and shared memory
#   --block-log-source-dir=PATH     Directory containing block_log
#   --config-ini-source=PATH        Path to config.ini file
#   --run-script=PATH               Path to run script (default: auto-detect)
#   --stop-at-block=N               Block number to stop at (default: 5000000)
#   --help                          Display this help screen
#
# Environment variables:
#   HIVE_NETWORK_TYPE               Network type (mainnet/testnet/mirrornet)
#   HAF_CI_MODE                     Set to 1 for HAF CI mode
#   COMMON_CI_CONFIG_REF            Git ref for fetching scripts (default: develop)

set -xeuo pipefail

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

# Try to source common.sh if available (for logging), but don't fail if missing
export LOG_FILE="${LOG_FILE:-build_data.log}"
if [[ -f "$SCRIPTPATH/../common.sh" ]]; then
    # shellcheck source=/dev/null
    source "$SCRIPTPATH/../common.sh"
elif [[ -f "$SCRIPTPATH/common.sh" ]]; then
    # shellcheck source=/dev/null
    source "$SCRIPTPATH/common.sh"
fi

IMG=""
DATA_CACHE=""
CONFIG_INI_SOURCE=""
BLOCK_LOG_SOURCE_DIR=""
RUN_SCRIPT=""
STOP_AT_BLOCK="${STOP_AT_BLOCK:-5000000}"
COMMON_CI_REF="${COMMON_CI_CONFIG_REF:-develop}"

print_help () {
    echo "Usage: $0 <image> [OPTION[=VALUE]]..."
    echo
    echo "Prepares blockchain data for CI testing by running a replay."
    echo
    echo "OPTIONS:"
    echo "  --data-cache=PATH             Directory where data and shared memory should be stored"
    echo "  --block-log-source-dir=PATH   Directory containing block_log for initial replay"
    echo "  --config-ini-source=PATH      Path to config.ini configuration file"
    echo "  --run-script=PATH             Path to run script (e.g., run_hived_img.sh)"
    echo "  --stop-at-block=N             Block number to stop replay at (default: 5000000)"
    echo "  --help                        Display this help screen and exit"
    echo
    echo "ENVIRONMENT:"
    echo "  HIVE_NETWORK_TYPE             Network type: mainnet, testnet, or mirrornet"
    echo "  HAF_CI_MODE                   Set to 1 to enable HAF CI mode"
    echo "  COMMON_CI_CONFIG_REF          Git ref for common-ci-configuration (default: develop)"
    echo
}

while [ $# -gt 0 ]; do
  case "$1" in
    --data-cache=*)
        DATA_CACHE="${1#*=}"
        echo "using DATA_CACHE $DATA_CACHE"
        ;;
    --block-log-source-dir=*)
        BLOCK_LOG_SOURCE_DIR="${1#*=}"
        echo "using BLOCK_LOG_SOURCE_DIR $BLOCK_LOG_SOURCE_DIR"
        ;;
    --config-ini-source=*)
        CONFIG_INI_SOURCE="${1#*=}"
        echo "using CONFIG_INI_SOURCE $CONFIG_INI_SOURCE"
        ;;
    --run-script=*)
        RUN_SCRIPT="${1#*=}"
        echo "using RUN_SCRIPT $RUN_SCRIPT"
        ;;
    --stop-at-block=*)
        STOP_AT_BLOCK="${1#*=}"
        echo "using STOP_AT_BLOCK $STOP_AT_BLOCK"
        ;;
    --help)
        print_help
        exit 0
        ;;
    *)
        if [ -z "$IMG" ]; then
          IMG="$1"
        else
          echo "ERROR: '$1' is not a valid option/positional argument"
          echo
          print_help
          exit 2
        fi
        ;;
    esac
    shift
done

if [[ -z "$IMG" ]]; then
    echo "ERROR: Docker image is required"
    print_help
    exit 1
fi

if [[ -z "$DATA_CACHE" ]]; then
    echo "ERROR: --data-cache is required"
    print_help
    exit 1
fi

# Auto-detect run script if not specified
if [[ -z "$RUN_SCRIPT" ]]; then
    # Try common locations
    for candidate in \
        "$SCRIPTPATH/../run_hived_img.sh" \
        "$SCRIPTPATH/run_hived_img.sh" \
        "${SCRIPTS_PATH:-}/run_hived_img.sh" \
        "${SUBMODULE_DIR:-}/scripts/run_hived_img.sh" \
        "${CI_PROJECT_DIR:-}/hive/scripts/run_hived_img.sh" \
        "${CI_PROJECT_DIR:-}/scripts/run_hived_img.sh"; do
        if [[ -x "$candidate" ]]; then
            RUN_SCRIPT="$candidate"
            echo "Auto-detected RUN_SCRIPT: $RUN_SCRIPT"
            break
        fi
    done

    if [[ -z "$RUN_SCRIPT" ]]; then
        # Fallback: fetch run_hived_img.sh and common.sh from hive repo
        echo "run_hived_img.sh not found locally, fetching from hive repo..."
        HIVE_SCRIPTS_REF="${HIVE_SCRIPTS_REF:-develop}"
        HIVE_RAW_URL="https://gitlab.syncad.com/hive/hive/-/raw/${HIVE_SCRIPTS_REF}/scripts"
        HIVE_SCRIPTS_DIR="/tmp/hive-scripts"
        mkdir -p "$HIVE_SCRIPTS_DIR"

        # Fetch run_hived_img.sh and its dependency common.sh
        for script in run_hived_img.sh common.sh; do
            echo "Fetching $script from hive@${HIVE_SCRIPTS_REF}..."
            curl -fsSL "${HIVE_RAW_URL}/${script}" -o "$HIVE_SCRIPTS_DIR/$script"
            chmod +x "$HIVE_SCRIPTS_DIR/$script"
        done

        RUN_SCRIPT="$HIVE_SCRIPTS_DIR/run_hived_img.sh"
        echo "Using fetched run_hived_img.sh from: $RUN_SCRIPT"
    fi
fi

# Wait for any other replay to finish
while [[ -f "$DATA_CACHE/replay_running" ]]; do
  echo "Another replay is running in $DATA_CACHE. Waiting for it to end..."
  sleep 60
done

# Check if previous replay is valid
if [[ -f "$DATA_CACHE/datadir/status" ]]; then
    echo "Previous replay exit code"
    status=$(cat "$DATA_CACHE/datadir/status")
    echo "$status"
    if [ "$status" -eq 0 ]; then
        echo "Previous replay datadir is valid, exiting"
        exit 0
    fi
fi

touch "$DATA_CACHE/replay_running"

echo "Didn't find valid previous replay, performing fresh replay"
ls "$DATA_CACHE" -lath 2>/dev/null || true
ls "$DATA_CACHE/datadir" -lath 2>/dev/null || true

# Use rm without sudo - sudo fails on NFS due to root_squash
rm "$DATA_CACHE/datadir" -rf || sudo rm "$DATA_CACHE/datadir" -rf || true
rm "$DATA_CACHE/shm_dir" -rf || sudo rm "$DATA_CACHE/shm_dir" -rf || true

# Fetch prepare_data_and_shm_dir.sh from common-ci-configuration
PREPARE_SCRIPT="/tmp/prepare_data_and_shm_dir.sh"
echo "Fetching prepare_data_and_shm_dir.sh from common-ci-configuration (ref: $COMMON_CI_REF)"
curl -fsSL "https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/${COMMON_CI_REF}/scripts/prepare_data_and_shm_dir.sh" -o "$PREPARE_SCRIPT"
chmod +x "$PREPARE_SCRIPT"

echo "Preparing datadir and shm_dir in location ${DATA_CACHE}"
"$PREPARE_SCRIPT" --data-base-dir="$DATA_CACHE" \
    --block-log-source-dir="$BLOCK_LOG_SOURCE_DIR" \
    --config-ini-source="$CONFIG_INI_SOURCE"

echo "Attempting to perform replay using image ${IMG}..."

# Build docker volume arguments
DOCKER_VOLUMES=(
    --docker-option=--volume="$DATA_CACHE":"$DATA_CACHE"
)

# Mount block_log source directory if specified (needed for symlinks to work in container)
if [[ -n "$BLOCK_LOG_SOURCE_DIR" ]] && [[ -d "$BLOCK_LOG_SOURCE_DIR" ]]; then
    DOCKER_VOLUMES+=(--docker-option=--volume="$BLOCK_LOG_SOURCE_DIR":"$BLOCK_LOG_SOURCE_DIR":ro)
    echo "Mounting block_log source directory: $BLOCK_LOG_SOURCE_DIR (read-only)"
fi

"$RUN_SCRIPT" --name=hived_instance \
    --detach \
    "${DOCKER_VOLUMES[@]}" \
    --data-dir="$DATA_CACHE/datadir" \
    --shared-file-dir="$DATA_CACHE/shm_dir" \
    --docker-option=--env=HIVED_UID="$(id -u)" \
    --docker-option=--env=HAF_CI_MODE="${HAF_CI_MODE:-0}" \
    "$IMG" --replay-blockchain --stop-at-block="$STOP_AT_BLOCK" --exit-before-sync

echo "Logs from container hived_instance:"
docker logs -f hived_instance &

status=$(docker wait hived_instance)

echo "HIVED_UID=$(id -u)" > "$DATA_CACHE/datadir/hived_uid.env"

echo "$status" > "$DATA_CACHE/datadir/status"

rm "$DATA_CACHE/replay_running" -f

exit "$status"
