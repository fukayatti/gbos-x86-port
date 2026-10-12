#!/bin/bash
# make wrapper for the Googlebook x86_64 kernel (AOSP kernel/common, tag android17-6.18-2026-06_r52).
# Rust must match what Google shipped (rustc 1.91.1, bindgen 0.72.1) or Rust Binder / ashmem silently drop out.
TC="$HOME/.rustup/toolchains/1.91.1-x86_64-unknown-linux-gnu"
export PATH="$TC/bin:$HOME/.cargo/bin:/usr/lib/llvm-21/bin:$PATH"
# set but empty: no automatic "+" suffix (LOCALVERSION_AUTO is off, the full release is in CONFIG_LOCALVERSION)
# GKI computes symbol CRCs with gendwarfksyms --stable; without it every CRC differs from the shipped modules.
export KBUILD_GENDWARFKSYMS_STABLE=1
export LOCALVERSION=
cd "$HOME/src/kernel/common"
exec make O="$HOME/src/kernel/out" ARCH=x86_64 LLVM=1 \
  RUSTC="$TC/bin/rustc" BINDGEN="$HOME/.cargo/bin/bindgen" RUST_LIB_SRC="$TC/lib/rustlib/src/rust/library" "$@"
