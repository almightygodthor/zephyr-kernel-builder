#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
SUSFS_ENABLED="${2:-true}"

cd "$KERNEL_DIR"

# KernelSU-Next + SUSFS integration for the MT6895 5.10 AOSP-style tree.
# The manager fork is the known SUSFS-compatible dev-susfs tree. SUSFS itself
# comes from the upstream GitLab project and its Android 12 / 5.10 branch.
KSU_REPO="https://github.com/pershoot/KernelSU-Next.git"
KSU_REF="dev-susfs"
SUSFS_REPO="https://gitlab.com/simonpunk/susfs4ksu.git"
SUSFS_REF="gki-android12-5.10"

rm -rf KernelSU-Next susfs4ksu

echo "==> Cloning KernelSU-Next: ${KSU_REF}"
git clone --depth=1 --branch "$KSU_REF" "$KSU_REPO" KernelSU-Next

if [ ! -f KernelSU-Next/kernel/setup.sh ]; then
  echo "KernelSU-Next setup.sh not found"
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

  echo "==> Installing KernelSU-Next"
  bash KernelSU-Next/kernel/setup.sh dev-susfs

  # The Oplus build config is merged by build.config.oplus6895. Add the
  # required root/SUSFS symbols to that temporary fragment instead of
  # modifying the upstream kernel repository.
  CONFIG_FRAGMENT="kernel/configs/oplus6895.config"
  {
    echo
    echo "# KernelSU-Next + SUSFS"
    echo "CONFIG_KSU=y"
    echo "CONFIG_KPROBES=y"
    echo "CONFIG_KPROBE_EVENTS=y"
    echo "CONFIG_KSU_KPROBE_HOOKS=y"
    echo "CONFIG_KSU_SUSFS=y"
    echo "CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y"
  } >> "$CONFIG_FRAGMENT"
else
  echo "==> Installing KernelSU-Next"
  bash KernelSU-Next/kernel/setup.sh dev-susfs
  printf '\n# KernelSU-Next\nCONFIG_KSU=y\n' >> kernel/configs/oplus6895.config
fi

echo "==> Verifying integration"
test -d KernelSU-Next/kernel
grep -R "config KSU" -n KernelSU-Next/kernel/Kconfig 2>/dev/null || true
grep -E 'CONFIG_KSU=|CONFIG_KSU_SUSFS=|CONFIG_KSU_KPROBE_HOOKS=' kernel/configs/oplus6895.config || true
