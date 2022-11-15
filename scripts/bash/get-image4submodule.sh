#! /bin/bash
# shellcheck source-path=SCRIPTDIR

set -euo pipefail

SCRIPTPATH=$(realpath "$0")
SCRIPTDIR=$(dirname "$SCRIPTPATH")

IMGNAME="data"

source "$SCRIPTDIR/docker-image-utils.sh"

echo -e "\e[0Ksection_start:$(date +%s):input_check[collapsed=true]\r\e[0KChecking get-image4submodule.sh script input..."
SUBMODULE_PATH=${1:?"Missing argument #1: submodule path"}
shift
REGISTRY=${1:?"Missing argument #2: image registry"}
shift
DOTENV_VAR_NAME=${1:?"Missing argument #3: dot-env name"}
shift
REGISTRY_USER=${1:?"Missing argument #4: registry user"}
shift
REGISTRY_PASSWORD=${1:?"Missing argument #5: registry password"}
shift
BINARY_CACHE_PATH=${1:?"Missing argument #6: binary cache path"}
shift
REPOSITORY=${1:?"Missing argument #7: repository URL"}
shift
echo -e "\e[0Ksection_end:$(date +%s):input_check\r\e[0K"

retrieve_submodule_commit () {
  local -r p="${1}"
  pushd "$p" >/dev/null 2>&1
  local -r commit=$( git rev-parse HEAD )

  popd >/dev/null 2>&1

  echo "$commit"
}

echo -e "\e[0Ksection_start:$(date +%s):retrieve_submodule_commit[collapsed=true]\r\e[0KRetrieving submodule commit..."
echo "Attempting to get commit for: $SUBMODULE_PATH"
commit=$( retrieve_submodule_commit "${SUBMODULE_PATH}" )
img=$( build_image_name $IMGNAME "$commit" "$REGISTRY" )
img_path=$( build_image_registry_path $IMGNAME "$commit" "$REGISTRY" )
img_tag=$( build_image_registry_tag $IMGNAME "$commit" "$REGISTRY" )
echo -e "\e[0Ksection_end:$(date +%s):retrieve_submodule_commit\r\e[0K"

echo -e "\e[0Ksection_start:$(date +%s):docker_login[collapsed=true]\r\e[0KLogging in to Docker repository..."
echo "$REGISTRY_PASSWORD" | docker login -u "$REGISTRY_USER" "$REGISTRY" --password-stdin
echo -e "\e[0Ksection_end:$(date +%s):docker_login\r\e[0K"

echo -e "\e[0Ksection_start:$(date +%s):image_exists[collapsed=true]\r\e[0KChecking if image exists..."
image_exists=0
docker_image_exists "$IMGNAME" "$commit" "$REGISTRY" image_exists
echo -e "\e[0Ksection_end:$(date +%s):image_exists\r\e[0K"

if [ "$image_exists" -eq 1 ];
then
  echo "Image already exists..."
  "$SCRIPTDIR/export-binaries.sh" "${img}" "${BINARY_CACHE_PATH}"
else
  # Here continue an image build.
  echo "Image ${img} is missing. Building it..."
  "$SCRIPTDIR/build-data4commit.sh" "$commit" "$REGISTRY" "$REPOSITORY" --export-binaries="${BINARY_CACHE_PATH}"
  echo -e "\e[0Ksection_start:$(date +%s):image_push[collapsed=true]\r\e[0KPusing data image to Docker repository..."
  time docker push "$img"
  echo -e "\e[0Ksection_end:$(date +%s):image_push\r\e[0K"
fi

echo -e "\e[0Ksection_start:$(date +%s):dot_env[collapsed=true]\r\e[0KGenerating dotenv file..."
echo "$DOTENV_VAR_NAME=$img" > docker_image_name.env
echo "${DOTENV_VAR_NAME}_REGISTRY_PATH=$img_path" >> docker_image_name.env
echo "${DOTENV_VAR_NAME}_REGISTRY_TAG=$img_tag" >> docker_image_name.env
cat docker_image_name.env
echo -e "\e[0Ksection_end:$(date +%s):dot_env\r\e[0K"