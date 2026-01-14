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
  no-shared \
  no-asm \
  no-threads \
  no-ssl3 \
  no-dtls \
  no-dtls1 \
  no-engine \
  no-dso \
  no-dsa \
  no-dh \
  no-ec2m \
  no-sm2 \
  no-sm3 \
  no-sm4 \
  no-idea \
  no-mdc2 \
  no-rc2 \
  no-rc4 \
  no-rc5 \
  no-bf \
  no-cast \
  no-camellia \
  no-seed \
  no-aria \
  no-chacha \
  no-poly1305 \
  no-siphash \
  no-whirlpool \
  no-scrypt \
  no-cms \
  no-ocsp \
  no-srp \
  no-psk \
  no-ts \
  no-ct \
  no-comp \
  no-ssl \
  no-tls \
  no-stdio \
  no-sock \
  no-filenames \
  no-autoerrinit \
  no-err \
  linux-x32 \
  -static

# shellcheck disable=SC2016
sed -i 's/$(CROSS_COMPILE)//' Makefile
emmake make -j 8 CFLAGS="-Oz -fvisibility=hidden -ffunction-sections -fdata-sections" CXXFLAGS="-Oz -fvisibility=hidden -ffunction-sections -fdata-sections" LDFLAGS="-Oz --gc-sections"
emmake make install

echo "OpenSSL build finished."