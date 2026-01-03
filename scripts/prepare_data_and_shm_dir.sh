#! /bin/bash
set -xeuo pipefail
shopt -s nullglob

while [ $# -gt 0 ]; do
  case "$1" in
    --data-base-dir=*)
        DATA_BASE_DIR="${1#*=}"
        echo "using DATA_BASE_DIR $DATA_BASE_DIR"
        ;;
    --block-log-source-dir=*)
        BLOCK_LOG_SOURCE_DIR="${1#*=}"
        echo "block-log-source-dir $BLOCK_LOG_SOURCE_DIR"
        ;;
    --config-ini-source=*)
        CONFIG_INI_SOURCE="${1#*=}"
        echo "config-ini $CONFIG_INI_SOURCE"
        ;;
    *)
        echo "ERROR: '$1' is not a valid option/positional argument"
        echo
        exit 2
    esac
    shift
done


if [ -z $DATA_BASE_DIR ];
then
  echo "No DATA_BASE_DIR directory privided, skipping this script"
  exit 1
else
  mkdir -p $DATA_BASE_DIR/datadir
  mkdir -p $DATA_BASE_DIR/shm_dir
fi

function handle_single_file_of_block_log() {
  local FILE_PATH=$1
  local FILE_NAME=$(basename -- "$FILE_PATH")

  mkdir -p $DATA_BASE_DIR/datadir/blockchain

  if [ -n "${HIVE_NETWORK_TYPE+x}" ] && [ "$HIVE_NETWORK_TYPE" = mirrornet ];
  then
    echo "creating copy of block log file as mirrornet block log can't be shared between pipelines"
    cp "$FILE_PATH" "$DATA_BASE_DIR/datadir/blockchain/"
  else
    # Try hardlink first (fastest, works on same filesystem)
    echo "creating hardlink of $FILE_PATH in $DATA_BASE_DIR/datadir/blockchain/"
    if ! ln "$FILE_PATH" "$DATA_BASE_DIR/datadir/blockchain/$FILE_NAME" 2>/dev/null; then
      # Hardlink failed (likely cross-device), create symlink to the source directory.
      # This symlink uses the absolute path to the source, which must be mounted in
      # any container that needs to access it.
      echo "Hardlink failed (likely cross-device), creating symlink instead"
      ln -s "$FILE_PATH" "$DATA_BASE_DIR/datadir/blockchain/$FILE_NAME"
    fi
  fi

  if [ -e $FILE_PATH.artifacts ];
  then
    cp $FILE_PATH.artifacts $DATA_BASE_DIR/datadir/blockchain/$FILE_NAME.artifacts
  fi
}

if [ -n "$BLOCK_LOG_SOURCE_DIR" ]; then
  if [ -e $BLOCK_LOG_SOURCE_DIR/block_log ]; then
    handle_single_file_of_block_log "$BLOCK_LOG_SOURCE_DIR/block_log"
  fi
  if ls $BLOCK_LOG_SOURCE_DIR/block_log_part.???? 1>/dev/null 2>&1; then
    for TARGET_FILE in $BLOCK_LOG_SOURCE_DIR/block_log_part.????; do
      handle_single_file_of_block_log "$TARGET_FILE"
    done
  fi
fi


# Copy config.ini if source is specified
if [ -n "$CONFIG_INI_SOURCE" ]; then
  echo "Copying config from: $CONFIG_INI_SOURCE"
  if [ -f "$CONFIG_INI_SOURCE" ]; then
    cp "$CONFIG_INI_SOURCE" "$DATA_BASE_DIR/datadir/config.ini"
    if [ -f "$DATA_BASE_DIR/datadir/config.ini" ]; then
      echo "Config copied successfully to $DATA_BASE_DIR/datadir/config.ini"
      # Show key settings for verification
      grep -E "shared-file-size|shared-file-full-threshold" "$DATA_BASE_DIR/datadir/config.ini" || true
    else
      echo "ERROR: Config copy failed - destination file not found"
      exit 1
    fi
  else
    echo "ERROR: Config source file not found: $CONFIG_INI_SOURCE"
    exit 1
  fi
else
  echo "WARNING: CONFIG_INI_SOURCE not specified - hived will use default config (larger shared memory)"
fi
