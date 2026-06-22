# CMake toolchain file for cross-compiling to aarch64 with the buildroot
# toolchain shipped in ci-base-image (and produced by build-toolchain.sh).
#
# CROSS_ROOT must point at the buildroot "host" output directory, i.e. the dir
# containing bin/<triple>-gcc and <triple>/sysroot. It is taken from the
# CROSS_ROOT environment variable (set by ci-base-image / hive's build_arm.sh),
# falling back to the path baked into ci-base-image.

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_VERSION 1)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CROSS_TRIPPLE aarch64-buildroot-linux-gnu)

if(DEFINED ENV{CROSS_ROOT})
  set(CROSS_ROOT $ENV{CROSS_ROOT})
else()
  set(CROSS_ROOT /opt/hive/cross/aarch64)
endif()

if(NOT EXISTS "${CROSS_ROOT}/bin/${CROSS_TRIPPLE}-gcc")
  message(FATAL_ERROR "aarch64 cross toolchain not found under CROSS_ROOT='${CROSS_ROOT}'. "
                      "Set the CROSS_ROOT environment variable to the buildroot output/host directory.")
endif()

set(CMAKE_C_COMPILER ${CROSS_ROOT}/bin/${CROSS_TRIPPLE}-gcc)
set(CMAKE_CXX_COMPILER ${CROSS_ROOT}/bin/${CROSS_TRIPPLE}-g++)

set(CMAKE_CXX_FLAGS "-I ${CROSS_ROOT}/include/")

list(APPEND CMAKE_FIND_ROOT_PATH ${CMAKE_PREFIX_PATH} ${CROSS_ROOT} ${CROSS_ROOT}/${CROSS_TRIPPLE})
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)

set(CMAKE_SYSROOT ${CROSS_ROOT}/${CROSS_TRIPPLE}/sysroot)

# Optional qemu emulator (only used for try_run() during configure). Honor an
# explicit override, otherwise auto-detect qemu-aarch64 / qemu-aarch64-static.
if(DEFINED ENV{QEMU_AARCH64})
  set(CMAKE_CROSSCOMPILING_EMULATOR $ENV{QEMU_AARCH64})
else()
  find_program(_qemu_aarch64 NAMES qemu-aarch64 qemu-aarch64-static)
  if(_qemu_aarch64)
    set(CMAKE_CROSSCOMPILING_EMULATOR ${_qemu_aarch64})
  endif()
endif()
