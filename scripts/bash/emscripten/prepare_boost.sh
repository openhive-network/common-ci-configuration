#! /bin/bash

set -xeuo pipefail

TMP_SRC=${1:?"Missing arg #1 to specify source temp directory"}
INSTALL_PREFIX=${2:?"Missing arg #2 to specify prebuilt libraries install prefix"}

echo "Entering directory: ${TMP_SRC}/boost/tools/build"

cd "${TMP_SRC}/boost/tools/build"

# Clean local mods if any
git checkout .
# to fix ambigous generators specific to SEARCH_LIB
git apply /home/emscripten/scripts/emscripten_toolset.patch

rm -vrf "${TMP_SRC}/boost_build/"

mkdir -vp "${TMP_SRC}/boost_build/"

echo "Entering directory: ${TMP_SRC}/boost"

cd "${TMP_SRC}/boost"

./bootstrap.sh --without-icu --prefix="${INSTALL_PREFIX}"

printf "using clang : emscripten : emcc -s USE_ZLIB=1 -s USE_ICU=0 : <archiver>emar <ranlib>emranlib <linker>emlink <cxxflags>\"-std=c++11 -s USE_ICU=0\" ;" | tee -a ./project-config.jam >/dev/null

./b2 \
  --build-dir="${TMP_SRC}/boost_build/" \
  --prefix="${INSTALL_PREFIX}" \
  -j "$(nproc)" \
  -q \
  runtime-link=static \
  link=static \
  toolset=clang-emscripten \
  variant=release \
  threading=single \
  --with-atomic \
  --with-chrono \
  --with-date_time \
  --with-filesystem \
  --with-program_options \
  --with-regex \
  --with-system \
  install
