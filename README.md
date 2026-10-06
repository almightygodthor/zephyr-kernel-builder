# Zephyr Kernel Builder

Kernel build automation for Realme GT Neo 3 (Zephyr / MT6895).

## Target

- Kernel: 5.10
- Defconfig: oplus6895_defconfig
- Build system: Android kernel build with the supplied Oplus manifest/build configuration
- Output: Image.gz
- Root flavor: KernelSU-Next + SUSFS
- Packaging: AnyKernel3 ZIP with timestamped identification
- Notifications: Telegram

## Notes

Secrets are provided through GitHub Actions repository secrets. Do not commit bot tokens or other credentials.

Maintainer: almightygodthor
