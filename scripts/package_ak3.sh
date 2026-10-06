#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
OUT_DIR="${2:?build output directory required}"
ROOT_IMPL="${3:-ksu-next}"
SUSFS_ENABLED="${4:-true}"

# build/build.sh uses O="$OUT_DIR/kernel-5.10", so the generated image lives there.
IMAGE="$OUT_DIR/kernel-5.10/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || IMAGE="$OUT_DIR/../kernel-5.10/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || IMAGE="$KERNEL_DIR/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || { echo "Image.gz not found"; exit 1; }

mkdir -p artifacts
TS="$(date -u +%Y%m%d-%H%M)"
ROOT_LABEL="NoRoot"
[ "$ROOT_IMPL" = "ksu-next" ] && ROOT_LABEL="KSU-Next"
SUSFS_LABEL="NoSUSFS"
[ "$SUSFS_ENABLED" = "true" ] && SUSFS_LABEL="SUSFS"
NAME="Zephyr-${ROOT_LABEL}-${SUSFS_LABEL}-${TS}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Use Thor's maintained AnyKernel3 fork.
if [ ! -d anykernel3 ]; then
  git clone --depth=1 https://github.com/almightygodthor/AnyKernel3.git anykernel3
fi

rsync -a --delete anykernel3/ "$WORK/"
cp "$IMAGE" "$WORK/Image.gz"

test -f "$WORK/anykernel.sh" || { echo "AnyKernel3 template missing"; exit 1; }

# Avoid changing unrelated AK3 defaults. The image is the only kernel payload.
(
  cd "$WORK"
  zip -r9 "$OLDPWD/artifacts/${NAME}.zip" . -x '*.git*' >/dev/null
)
rm -f "artifacts/Image.gz"
printf '%s\n' "$NAME" > artifacts/BUILD_NAME
printf '%s\n' "$TS" > artifacts/BUILD_TIME_UTC
printf 'Created %s\n' "$NAME"
