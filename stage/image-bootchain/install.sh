#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
# Fail closed if distro_bootcmd could select a higher-priority alternate loader.
for alternate in boot.scr boot.scr.uimg boot/extlinux/extlinux.conf extlinux/extlinux.conf boot/boot.scr.uimg; do
  [[ ! -e "$rootfs/$alternate" ]] || { echo "Alternate boot entry requires review: $alternate" >&2; exit 1; }
done
# Package updates may regenerate Armbian boot scripts; install our selector last.
mkimage -A arm64 -O linux -T script -C none -a 0 -e 0 -n 'Network Exit A/B' -d "$project/stage/image-bootchain/config/network-exit.cmd" "$rootfs/boot/boot.scr"
cp "$project/stage/image-bootchain/config/network-exit.cmd" "$rootfs/boot/boot.cmd"
# Validate the active kernel after all build-time security updates too.
kernel_image=$(readlink -- "$rootfs/boot/Image")
[[ $kernel_image == vmlinuz-* && $kernel_image != */* ]] || exit 1
kernel_config="$rootfs/boot/config-${kernel_image#vmlinuz-}"
for option in WATCHDOG WATCHDOG_SYSFS SUNXI_WATCHDOG SQUASHFS BLK_DEV_LOOP; do
  grep -qx "CONFIG_${option}=y" "$kernel_config" || { echo "Missing built-in CONFIG_${option}=y" >&2; exit 1; }
done
python3 - "$rootfs" <<'INITRD'
from pathlib import Path
import struct,sys,zlib
root=Path(sys.argv[1])
# Confirm Armbian's hook regenerated the legacy U-Boot initramfs wrapper.
wrapped=(root/'boot/uInitrd').read_bytes()
header=bytearray(wrapped[:64])
magic,crc=struct.unpack_from('>II',header)
struct.pack_into('>I',header,4,0)
assert magic == 0x27051956 and zlib.crc32(header) == crc, 'Invalid U-Boot initramfs header'
size,expected=struct.unpack_from('>II',wrapped,12)[0],struct.unpack_from('>I',wrapped,24)[0]
payload=wrapped[64:]
assert len(payload) == size and zlib.crc32(payload) == expected, 'Invalid U-Boot initramfs payload'
kernel=(root/'boot/Image').readlink().name.removeprefix('vmlinuz-')
assert payload == (root/f'boot/initrd.img-{kernel}').read_bytes(), 'Stale U-Boot initramfs'
INITRD
}
