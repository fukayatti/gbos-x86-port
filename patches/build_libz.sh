#!/bin/bash
# Rebuild AOSP external/zlib (Chromium zlib "1.3.2.1-motley") as a baseline x86-64 libz.so:
# same sources/defines as Android.bp, but without the platform's Panther-Lake-class -march, so the
# compiler does not auto-vectorize with AVX-VNNI/GFNI. Explicit SIMD paths stay behind CPUID checks.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$HERE/src"; OUT="$HERE/out"; mkdir -p "$OUT/obj"
NDK="${ANDROID_NDK:-$HOME/Android/android-ndk-r30}/toolchains/llvm/prebuilt/linux-x86_64/bin"
CC="$NDK/x86_64-linux-android35-clang"
DEFS="-DHAVE_HIDDEN -DZLIB_CONST -DCHROMIUM_ZLIB_NO_CASTAGNOLI -DADLER32_SIMD_SSSE3 -DDEFLATE_SLIDE_HASH_SSE2 -DINFLATE_CHUNK_SIMD_SSE2 -DCRC32_SIMD_SSE42_PCLMUL -DDEFLATE_CHUNK_WRITE_64LE -DDEFLATE_COMPARE256_64LE -DINFLATE_CHUNK_READ_64LE -DX86_NOT_WINDOWS"
CFLAGS="-O3 -fPIC -fno-semantic-interposition -mssse3 -msse4.2 -mpclmul -Wno-deprecated-non-prototype -Wno-unused -I$SRC"
SRCS="adler32.c adler32_simd.c compress.c cpu_features.c crc32.c crc32_simd.c crc_folding.c deflate.c gzclose.c gzlib.c gzread.c gzwrite.c infback.c inffast.c inftrees.c trees.c uncompr.c zutil.c contrib/optimizations/inffast_chunk.c contrib/optimizations/inflate.c"
OBJS=""
for s in $SRCS; do o="$OUT/obj/$(echo $s | tr '/' '_').o"; $CC $CFLAGS $DEFS -c "$SRC/$s" -o "$o"; OBJS="$OBJS $o"; done
$CC -shared -o "$OUT/libz.so" $OBJS -Wl,-soname,libz.so -Wl,-z,max-page-size=16384 -Wl,-z,relro -Wl,-z,now -Wl,--build-id=sha1 -lm -ldl
"$NDK/llvm-strip" --strip-unneeded "$OUT/libz.so"
ls -l "$OUT/libz.so"
