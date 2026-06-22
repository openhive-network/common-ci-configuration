#!/usr/bin/env bash
# Build the aarch64 cross toolchain used to cross-compile hived for ARM.
#
# Produces a buildroot "host" tree (compilers + aarch64 sysroot) at OUTPUT_DIR,
# with Toolchain.cmake placed at its root so it can be used directly as:
#   cmake --toolchain "$OUTPUT_DIR/Toolchain.cmake" ...
#
# This encapsulates (and fixes the documented gaps of) the procedure in
# hive/doc/building.md "Support for ARM":
#   - the buildroot patches are actually applied (git apply), not just copied
#   - boost is bumped to 1.88.0 to match the regular x64 build
#
# Must run as a NON-root user (buildroot refuses to build several packages as root).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" >/dev/null 2>&1 && pwd -P)"

BUILDROOT_VERSION="${BUILDROOT_VERSION:-2025.02.4}"
ASSETS_DIR="${ASSETS_DIR:-$SCRIPT_DIR}"
WORK_DIR="${WORK_DIR:-/tmp/buildroot-build}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/hive/cross/aarch64}"
SLIM="${SLIM:-1}"

if [ "$(id -u)" = "0" ]; then
  echo "ERROR: do not run buildroot as root. Use a regular user." >&2
  exit 1
fi

echo "==> buildroot ${BUILDROOT_VERSION}: clone"
rm -rf "$WORK_DIR"
git clone --depth 1 --branch "${BUILDROOT_VERSION}" \
  https://github.com/buildroot/buildroot.git "$WORK_DIR"

cd "$WORK_DIR"

echo "==> apply hive buildroot config"
cp "$ASSETS_DIR/buildroot.config" .config

echo "==> apply patches (glibc downgrade, snappy static, boost 1.88)"
for p in glibc_downgrade.patch snappy_static_build.patch boost_1_88_upgrade.patch; do
  echo "    - $p"
  git apply "$ASSETS_DIR/patches/$p"
done

echo "==> normalize config"
make olddefconfig

echo "==> build toolchain (this downloads sources and compiles gcc/glibc/sysroot)"
# BR2_JLEVEL=0 in the config makes buildroot use all available cores.
make

HOST_DIR="$WORK_DIR/output/host"
test -x "$HOST_DIR/bin/aarch64-buildroot-linux-gnu-g++" \
  || { echo "ERROR: toolchain g++ missing after build" >&2; exit 1; }

echo "==> install Toolchain.cmake into toolchain root"
cp "$ASSETS_DIR/Toolchain.cmake" "$HOST_DIR/Toolchain.cmake"

if [ "$SLIM" = "1" ]; then
  echo "==> slim toolchain (drop docs / locales / man)"
  rm -rf "$HOST_DIR/share/doc" "$HOST_DIR/share/man" "$HOST_DIR/share/info" \
         "$HOST_DIR/share/locale" 2>/dev/null || true
fi

echo "==> publish toolchain to $OUTPUT_DIR"
mkdir -p "$(dirname "$OUTPUT_DIR")"
rm -rf "$OUTPUT_DIR"
# Copy (deref any internal symlinks left dangling outside the tree is avoided by -a).
cp -a "$HOST_DIR" "$OUTPUT_DIR"

echo "==> done: $OUTPUT_DIR"
"$OUTPUT_DIR/bin/aarch64-buildroot-linux-gnu-g++" --version | head -1
