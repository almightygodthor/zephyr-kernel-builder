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

if [ "$ROOT_IMPL" != "ksu-next" ] && [ "$ROOT_IMPL" != "kernel-su" ] && [ "$ROOT_IMPL" != "sukisu-ultra" ]; then
  echo "Unsupported root implementation: $ROOT_IMPL"
  exit 1
fi

# KernelSU-Next + SUSFS integration for the MT6895 5.10 AOSP-style tree.
# The manager fork is the known SUSFS-compatible dev-susfs tree. SUSFS itself
# comes from the upstream GitLab project and its Android 12 / 5.10 branch.


SUSFS_REPO="https://gitlab.com/simonpunk/susfs4ksu.git"
SUSFS_REF="gki-android12-5.10"

rm -rf KernelSU KernelSU-Next susfs4ksu

if [ "$ROOT_IMPL" = "sukisu-ultra" ]; then
  KSU_REPO="https://github.com/SukiSU-Ultra/SukiSU-Ultra.git"
  KSU_REF="builtin"
  KSU_DIR="KernelSU"
  echo "==> Cloning SukiSU-Ultra"
  git clone --depth=1 "$KSU_REPO" "$KSU_DIR"
  if [ "$SUSFS_ENABLED" = "true" ]; then
    KSU_REF="susfs-main"
  fi
  bash "$KSU_DIR/kernel/setup.sh" "$KSU_REF"
elif [ "$ROOT_IMPL" = "ksu-next" ]; then
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

if [ "$SUSFS_ENABLED" = "true" ] && [ "$ROOT_IMPL" != "sukisu-ultra" ]; then
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

  if [ "$ROOT_IMPL" != "sukisu-ultra" ]; then
    echo "==> Installing root implementation"
    if [ "$ROOT_IMPL" = "ksu-next" ]; then
      bash "$KSU_DIR/kernel/setup.sh" dev-susfs
    else
      bash "$KSU_DIR/kernel/setup.sh" "$KSU_REF"
    fi
  fi

  # The Oplus build config is merged by build.config.oplus6895. Add the
  # required root/SUSFS symbols to that temporary fragment instead of
  # modifying the upstream kernel repository.
  CONFIG_FRAGMENT="kernel/configs/oplus6895.config"
  {
    echo
    echo "# Root + SUSFS"
    echo "CONFIG_KSU=y"
    echo "CONFIG_KPROBES=y"
    echo "CONFIG_KPROBE_EVENTS=y"
    if [ "$ROOT_IMPL" = "ksu-next" ]; then
      echo "CONFIG_KSU_KPROBE_HOOKS=y"
    fi
    echo "CONFIG_KSU_SUSFS=y"
    echo "CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y"
  } >> "$CONFIG_FRAGMENT"
  if [ "$ROOT_IMPL" = "sukisu-ultra" ]; then
    printf "CONFIG_KSU_SUSFS=y\nCONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y\n" >> "$CONFIG_FRAGMENT"
  fi
else
  if [ "$ROOT_IMPL" = "sukisu-ultra" ]; then
    echo "==> SukiSU-Ultra already integrated via builtin"
    printf "\n# SukiSU-Ultra\nCONFIG_KSU=y\n" >> kernel/configs/oplus6895.config
  else
    echo "==> Installing root implementation"
    if [ "$ROOT_IMPL" = "ksu-next" ]; then
      bash "$KSU_DIR/kernel/setup.sh" dev-susfs
      printf "\n# KernelSU-Next\nCONFIG_KSU=y\n" >> kernel/configs/oplus6895.config
    else
      bash "$KSU_DIR/kernel/setup.sh" "$KSU_REF"
      printf "\n# KernelSU\nCONFIG_KSU=y\n" >> kernel/configs/oplus6895.config
    fi
  fi
fi

echo "==> Verifying integration"
test -d "$KSU_DIR/kernel"
grep -R "config KSU" -n "$KSU_DIR/kernel/Kconfig" 2>/dev/null || true
grep -E 'CONFIG_KSU=|CONFIG_KSU_SUSFS=|CONFIG_KSU_KPROBE_HOOKS=' kernel/configs/oplus6895.config || true
