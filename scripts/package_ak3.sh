#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
OUT_DIR="${2:?build output directory required}"

IMAGE="$OUT_DIR/../kernel-5.10/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || IMAGE="$KERNEL_DIR/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || { echo "Image.gz not found"; exit 1; }

mkdir -p artifacts
TS="\$(date -u +%Y%m%d-%H%M)"
NAME="Zephyr-KSU-Next-SUSFS-${TS}"
WORK="\$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# AK3 directory can be populated in the repository without kernel source files.
if [ ! -d anykernel3 ]; then
  git clone --depth=1 https://github.com/osm0sis/AnyKernel3.git anykernel3
fi

rsync -a --delete anykernel3/ "$WORK/"
cp "$IMAGE" "$WORK/Image.gz-dtb"

test -f "$WORK/anykernel.sh" || { echo "AnyKernel3 template missing"; exit 1; }

# Avoid changing unrelated AK3 defaults. The image is the only kernel payload.
(
  cd "$WORK"
  zip -r9 "$OLDPWD/artifacts/${NAME}.zip" . -x '*.git*' >/dev/null
)
cp "$IMAGE" "artifacts/Image.gz"
printf '%s\n' "$NAME" > artifacts/BUILD_NAME
printf '%s\n' "$TS" > artifacts/BUILD_TIME_UTC
printf 'Created %s\n' "$NAME"
