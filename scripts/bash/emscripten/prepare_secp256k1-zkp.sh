#! /bin/bash

set -xeuo pipefail

TMP_SRC=${1:?"Missing arg #1 to specify source temp directory"}
INSTALL_PREFIX=${2:?"Missing arg #2 to specify prebuilt libraries install prefix"}

echo "Entering directory: ${TMP_SRC}/secp256k1-zkp"

cd "${TMP_SRC}/secp256k1-zkp"

git checkout d22774e248c703a191049b78f8d04f37d6fcfa05

export VERBOSE=1
emconfigure ./autogen.sh
emconfigure ./configure --prefix=${INSTALL_PREFIX} --with-asm=no --enable-shared=no --enable-tests=no --enable-benchmark=no --enable-exhaustive-tests=no --with-pic=no --with-valgrind=no --enable-module-recovery=yes --enable-module-rangeproof=yes --enable-experimental
emmake make
emmake make install
