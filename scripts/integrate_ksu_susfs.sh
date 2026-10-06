#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
SUSFS_ENABLED="${2:-true}"

cd "$KERNEL_DIR"

# Pinned upstream sources make builds reproducible. These refs can be changed
# in one place after compatibility is verified for this kernel branch.
KSU_REPO="https://github.com/rifsxd/KernelSU-Next.git"
KSU_REF="master"
SUSFS_REPO="https://github.com/simonpunk/susfs4ksu.git"
SUSFS_REF="master"

if [ -d KernelSU-Next ]; then
  rm -rf KernelSU-Next
fi

git clone --depth=1 --branch "$KSU_REF" "$KSU_REPO" KernelSU-Next

if [ ! -x KernelSU-Next/kernel/setup.sh ]; then
  echo "KernelSU-Next setup script not found; source layout changed."
  exit 1
fi

# Use the project's official setup path when present.
bash KernelSU-Next/kernel/setup.sh

if [ "$SUSFS_ENABLED" = "true" ]; then
  rm -rf susfs4ksu
  git clone --depth=1 --branch "$SUSFS_REF" "$SUSFS_REPO" susfs4ksu

  if [ -f susfs4ksu/kernel_patches/50_add_susfs_in_gki-android12-5.10.patch ]; then
    patch -p1 < susfs4ksu/kernel_patches/50_add_susfs_in_gki-android12-5.10.patch
  elif [ -f susfs4ksu/kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch ]; then
    patch -p1 < susfs4ksu/kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch || true
  else
    echo "No known SUSFS patch for this tree was found."
    exit 1
  fi
fi

echo "Checking root-related config symbols..."
grep -E 'CONFIG_KSU|CONFIG_KSU_SUSFS|CONFIG_KSU_NEXT|CONFIG_SUSFS' arch/arm64/configs/oplus6895_defconfig || true
