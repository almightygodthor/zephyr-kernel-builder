#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
ROOT_IMPL="${2:-ksu-next}"
SUSFS_ENABLED="${3:-true}"

cd "$KERNEL_DIR"

if [ "$ROOT_IMPL" = "none" ]; then
  echo "==> Root integration disabled"
  exit 0
fi

case "$ROOT_IMPL" in
  ksu-next|kernel-su|sukisu-ultra) ;;
  *) echo "Unsupported root implementation: $ROOT_IMPL"; exit 1 ;;
esac

CONFIG_FRAGMENT="kernel/configs/oplus6895.config"
SUSFS_REPO="https://gitlab.com/simonpunk/susfs4ksu.git"
SUSFS_REF="gki-android12-5.10"

rm -rf KernelSU KernelSU-Next susfs4ksu

if [ "$ROOT_IMPL" = "sukisu-ultra" ]; then
  # SukiSU-Ultra carries its own kernel integration. Its builtin branch is
  # intended for kernels compiled into the image; susfs-main adds SUSFS.
  KSU_REPO="https://github.com/SukiSU-Ultra/SukiSU-Ultra.git"
  KSU_DIR="KernelSU"
  KSU_REF="builtin"
  [ "$SUSFS_ENABLED" = "true" ] && KSU_REF="susfs-main"

  echo "==> Cloning SukiSU-Ultra"
  git clone --depth=1 "$KSU_REPO" "$KSU_DIR"
  echo "==> Installing SukiSU-Ultra: $KSU_REF"
  bash "$KSU_DIR/kernel/setup.sh" "$KSU_REF"

  {
    echo
    echo "# SukiSU-Ultra"
    echo "CONFIG_KSU=y"
    echo "CONFIG_KPROBES=y"
    echo "CONFIG_KPROBE_EVENTS=y"
  } >> "$CONFIG_FRAGMENT"

  if [ "$SUSFS_ENABLED" = "true" ]; then
    echo "==> SukiSU-Ultra SUSFS is bundled in $KSU_REF"
    {
      echo "CONFIG_KSU_SUSFS=y"
      echo "CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y"
    } >> "$CONFIG_FRAGMENT"
  fi
else
  if [ "$ROOT_IMPL" = "ksu-next" ]; then
    KSU_REPO="https://github.com/pershoot/KernelSU-Next.git"
    KSU_REF="dev-susfs"
    KSU_DIR="KernelSU-Next"
    echo "==> Cloning KernelSU-Next"
    git clone --depth=1 --branch "$KSU_REF" "$KSU_REPO" "$KSU_DIR"
  else
    KSU_REPO="https://github.com/tiann/KernelSU.git"
    KSU_REF="$(git ls-remote --tags --refs "$KSU_REPO" 'v*' | sed 's#.*refs/tags/##' | sort -V | tail -n1)"
    [ -n "$KSU_REF" ] || { echo "Could not determine latest KernelSU tag"; exit 1; }
    KSU_DIR="KernelSU"
    echo "==> Cloning KernelSU"
    git clone --depth=1 "$KSU_REPO" "$KSU_DIR"
  fi

  if [ ! -f "$KSU_DIR/kernel/setup.sh" ]; then
    echo "KernelSU setup.sh not found"
    exit 1
  fi

  if [ "$SUSFS_ENABLED" = "true" ]; then
    echo "==> Cloning SUSFS: ${SUSFS_REF}"
    git clone --depth=1 --branch "$SUSFS_REF" "$SUSFS_REPO" susfs4ksu

    PATCH="susfs4ksu/kernel_patches/50_add_susfs_in_gki-android12-5.10.patch"
    [ -f "$PATCH" ] || {
      echo "Missing SUSFS 5.10 patch: $PATCH"
      exit 1
    }

    echo "==> Installing SUSFS source files"
    cp -f susfs4ksu/kernel_patches/fs/* fs/
    cp -f susfs4ksu/kernel_patches/include/linux/* include/linux/

    echo "==> Applying SUSFS kernel patch"
    patch -p1 -ui "$PATCH"

    echo "==> Installing root implementation"
    if [ "$ROOT_IMPL" = "ksu-next" ]; then
      bash "$KSU_DIR/kernel/setup.sh" dev-susfs
    else
      bash "$KSU_DIR/kernel/setup.sh" "$KSU_REF"
    fi

    {
      echo
      echo "# Root + SUSFS"
      echo "CONFIG_KSU=y"
      echo "CONFIG_KPROBES=y"
      echo "CONFIG_KPROBE_EVENTS=y"
      [ "$ROOT_IMPL" = "ksu-next" ] && echo "CONFIG_KSU_KPROBE_HOOKS=y"
      echo "CONFIG_KSU_SUSFS=y"
      echo "CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y"
    } >> "$CONFIG_FRAGMENT"
  else
    echo "==> Installing root implementation"
    if [ "$ROOT_IMPL" = "ksu-next" ]; then
      bash "$KSU_DIR/kernel/setup.sh" dev-susfs
      printf "\n# KernelSU-Next\nCONFIG_KSU=y\n" >> "$CONFIG_FRAGMENT"
    else
      bash "$KSU_DIR/kernel/setup.sh" "$KSU_REF"
      printf "\n# KernelSU\nCONFIG_KSU=y\n" >> "$CONFIG_FRAGMENT"
    fi
  fi
fi

echo "==> Verifying integration"
test -d "$KSU_DIR/kernel"
grep -R "config KSU" -n "$KSU_DIR/kernel/Kconfig" 2>/dev/null || true
grep -E 'CONFIG_KSU=|CONFIG_KSU_SUSFS=|CONFIG_KSU_KPROBE_HOOKS=' "$CONFIG_FRAGMENT" || true
