#!/bin/bash
set -xeuo pipefail

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

TARGET_DIR=${1:?"Missing arg #1 to specify target directory to save downloaded files"}

abs_target_dir=`realpath -m --relative-base="$SCRIPTPATH" "$TARGET_DIR"`
TARGET_DIR="${abs_target_dir}"

mkdir -vp "${TARGET_DIR}"

pushd "${TARGET_DIR}"

declare -A EXTENSION_LIST=(
  ["Hive-Keychain"]="jcacnejopjdphbnjgfaaobbfafkihpep"
) 

for i in "${!EXTENSION_LIST[@]}"; do
    # echo "Key: $i value: ${EXTlist[$i]}"
    extensionName=$i
    extensionID=${EXTENSION_LIST[$i]}

    # we could try to use Chrome documented way to specify extensions to be automatically installed, but it is not honored in headless version of Chromium
    # https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions#preference-linux
    #echo '{"external_update_url": "https://clients2.google.com/service/update2/crx"}' > /opt/google/chrome/extensions/${extensionID}.json

    DOWNLOAD_FILE_NAME="crx?response=redirect&os=win&arch=x86-64&os_arch=x86-64&nacl_arch=x86-64&prod=chromecrx&prodchannel=unknown&prodversion=9999.0.9999.0&acceptformat=crx2,crx3&x=id=${extensionID}&uc"
    DOWNLOAD_URL="https://clients2.google.com/service/update2/crx?response=redirect&os=win&arch=x86-64&os_arch=x86-64&nacl_arch=x86-64&prod=chromecrx&prodchannel=unknown&prodversion=9999.0.9999.0&acceptformat=crx2,crx3&x=id%3D${extensionID}%26uc"
    wget -nc "${DOWNLOAD_URL}"

    UNZIP_DIR="${TARGET_DIR}/${extensionName}"
    if [[ -d "${UNZIP_DIR}" ]];
    then
      echo "${extensionName} extension directory exists. Skipping..."
    else
      echo "Unpacking ${extensionName} into directory: ${UNZIP_DIR}"

      mkdir -vp "${UNZIP_DIR}"
      pushd "${UNZIP_DIR}"

      # unzip often failed with random errors, so instead of regular cp, let's try to fix it immediately
      zip -FFv "$TARGET_DIR/${DOWNLOAD_FILE_NAME}" --out "${extensionName}.zip"
      #cp -n "$TARGET_DIR/${DOWNLOAD_FILE_NAME}" "${extensionName}.zip"

      unzip "./${extensionName}.zip"
      popd
    fi
done

popd
