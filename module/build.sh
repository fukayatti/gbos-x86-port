#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HDR="${KERNEL_HEADERS:?set KERNEL_HEADERS to the kernel build dir (out/ with Module.symvers)}"

echo "Building gfni_emu.ko..."

# Compile gfni_emu.o
gcc -O2 -std=gnu11 -nostdinc -isystem $(gcc -print-file-name=include) \
  -I$HDR/arch/x86/include \
  -I$HDR/arch/x86/include/generated \
  -I$HDR/include \
  -I$HDR/include/generated \
  -I$HDR/arch/x86/include/uapi \
  -I$HDR/arch/x86/include/generated/uapi \
  -I$HDR/include/uapi \
  -I$HDR/include/generated/uapi \
  -include $HDR/include/linux/kconfig.h \
  -include $HDR/include/linux/compiler_types.h \
  -D__KERNEL__ -DMODULE -DKBUILD_MODNAME='"gfni_emu"' \
  -mno-sse -mno-avx \
  -c -o "$DIR/gfni_emu.o" "$DIR/gfni_emu.c"

# Compile gfni_emu.mod.o
gcc -O2 -std=gnu11 -nostdinc -isystem $(gcc -print-file-name=include) \
  -I$HDR/arch/x86/include \
  -I$HDR/arch/x86/include/generated \
  -I$HDR/include \
  -I$HDR/include/generated \
  -I$HDR/arch/x86/include/uapi \
  -I$HDR/arch/x86/include/generated/uapi \
  -I$HDR/include/uapi \
  -I$HDR/include/generated/uapi \
  -include $HDR/include/linux/kconfig.h \
  -include $HDR/include/linux/compiler_types.h \
  -D__KERNEL__ -DMODULE -DKBUILD_MODNAME='"gfni_emu"' \
  -c -o "$DIR/gfni_emu.mod.o" "$DIR/gfni_emu.mod.c"

# Link into gfni_emu.ko
ld -r -m elf_x86_64 -z noexecstack --no-warn-rwx-segments --build-id=sha1 \
  -T /usr/src/linux-headers-6.19.14/scripts/module.lds \
  -o "$DIR/gfni_emu.ko" \
  "$DIR/gfni_emu.o" \
  "$DIR/gfni_emu.mod.o"

echo "Successfully built $DIR/gfni_emu.ko!"
