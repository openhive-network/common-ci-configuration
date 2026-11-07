# aarch64 cross toolchain (for ci-base-image)

Definition of the buildroot-based **x86_64 → aarch64 cross toolchain** that
`ci-base-image` ships, used to cross-compile `hived` for ARM. This is the source
of truth for hive issue #831; the matching hive-side material is in
`hive/doc/aarch64`.

## Contents
- `buildroot.config` — buildroot 2025.02.4 config (aarch64 / cortex-a53, glibc,
  internal toolchain GCC 14.2.0 / binutils 2.43.1, target libs: Boost, OpenSSL,
  snappy, zlib, bzip2, readline, liburing, icu).
- `patches/` — applied to the buildroot tree before building:
  - `glibc_downgrade.patch` — pins glibc to 2.35 so binaries run on a wide range
    of arm64 runtimes.
  - `snappy_static_build.patch` — builds snappy as a static lib.
  - `boost_1_90_upgrade.patch` — bumps buildroot's Boost 1.83 → **1.90.0** to
    match the x64 ci-base-image build (Boost 1.90 changed the in-memory layout of
    containers such as `boost::container::deque`, so hived shared-memory state is
    only interchangeable between builds using the same Boost). (Boost `random` is
    also enabled in `buildroot.config` because b2 builds it transitively.
    `BR2_PACKAGE_BOOST_SYSTEM` stays selected by the other Boost options, but
    since 1.89 Boost.System is header-only and no `libboost_system` is built.)
- `Toolchain.cmake` — CMake toolchain file; honors `$CROSS_ROOT` (defaults to the
  baked image path `/opt/hive/cross/aarch64`).
- `build-toolchain.sh` — clones buildroot, applies config+patches, builds, and
  publishes `output/host` (+ `Toolchain.cmake`) to `$OUTPUT_DIR`.

## How it is built
`Dockerfile.ci-base-image` runs `build-toolchain.sh` in the
`aarch64-toolchain-builder` stage (as a non-root user — buildroot refuses root)
and `COPY`s the result to `/opt/hive/cross/aarch64` in the final image, exporting
`CROSS_ROOT=/opt/hive/cross/aarch64`.

## Using it
Inside ci-base-image (or with `CROSS_ROOT` set to the toolchain dir):

```bash
cmake --toolchain "$CROSS_ROOT/Toolchain.cmake" -B build_arm -S <hive-src> -GNinja
cmake --build build_arm --target hived
```

hive's `scripts/build_arm.sh` wraps this. The produced binary is a native aarch64
ELF (runs on ARM hardware, or under `qemu-aarch64`).

## Updating
- New buildroot release: change `BUILDROOT_VERSION` (Dockerfile arg /
  `build-toolchain.sh` default) and re-verify patches apply.
- Boost bump: update `patches/boost_1_90_upgrade.patch` (version + hash), rename
  it and its reference in `build-toolchain.sh`, and keep it in sync with the x64 Boost version in `Dockerfile.ci-base-image`.
- After any change, bump `CI_BASE_IMAGE_VERSION` in `docker-bake.hcl`.
