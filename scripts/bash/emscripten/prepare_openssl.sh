#! /bin/bash

echo "Building OpenSSL..."

set -xeuo pipefail

TMP_SRC=${1:?"Missing arg #1 to specify source temp directory"}
INSTALL_PREFIX=${2:?"Missing arg #2 to specify prebuilt libraries install prefix"}

echo "Entering directory: ${TMP_SRC}/openssl"

cd "${TMP_SRC}/openssl"

emconfigure ./Configure \
  --prefix="${INSTALL_PREFIX}" \
  --openssldir="${INSTALL_PREFIX}" \
  no-hw \
  no-shared \
  no-asm \
  no-threads \
  no-ssl3 \
  no-dtls \
  no-engine \
  no-dso \
  linux-x32 \
  -static

# shellcheck disable=SC2016
sed -i 's/$(CROSS_COMPILE)//' Makefile
emmake make -j 8 CFLAGS="-Oz" CXXFLAGS="-Oz" LDFLAGS="-Oz"
emmake make install

echo "OpenSSL build finished."