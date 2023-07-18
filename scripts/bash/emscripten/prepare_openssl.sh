#! /bin/bash

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

sed -i 's/$(CROSS_COMPILE)//' Makefile
emmake make -j 8 
emmake make install

