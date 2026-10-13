#!/bin/bash
# Build Mesa RADV (Vulkan for AMD, no LLVM) for Android x86_64 with the NDK. Needs the Mesa 26.2.4 source tree with
# patches/mesa-android-mapper5.patch from skylartaylor/gbos-vm applied, plus 0001-radv-two-graphics-queues-for-hwui.patch.
# usage: MESA_SRC=/path/to/patched/mesa ANDROID_NDK=/path/to/ndk build.sh <build-dir>
set -euo pipefail
: "${MESA_SRC:?}" "${ANDROID_NDK:?}"; B=${1:?build dir}; HERE=$(cd "$(dirname "$0")" && pwd)
sed "s#@NDK@#$ANDROID_NDK/toolchains/llvm/prebuilt/linux-x86_64#; s#@PKGCONFIG@#$(command -v pkg-config)#" "$HERE/android-x86_64.cross.in" > "$B.cross"
meson setup "$B" "$MESA_SRC" --cross-file "$B.cross" --buildtype=release -Dforce_fallback_for=libdrm,expat -Dallow-fallback-for=libdrm \
  --wrap-mode=nodownload -Dplatforms=android -Dplatform-sdk-version=34 -Dandroid-stub=true -Dandroid-libbacktrace=disabled -Dllvm=disabled \
  -Dglx=disabled -Dgbm=disabled -Dvideo-codecs= -Dgallium-va=disabled -Dzstd=disabled -Dgallium-drivers= -Dvulkan-drivers=amd -Degl=disabled \
  -Dgles1=disabled -Dgles2=disabled -Dopengl=false -Dlibdrm:amdgpu=enabled
ninja -C "$B" src/amd/vulkan/libvulkan_radeon.so
echo "install as /vendor/lib64/hw/vulkan.radv.so (stripped) plus subprojects/libdrm-*/amdgpu/libdrm_amdgpu.so"
