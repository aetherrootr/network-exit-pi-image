#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
# Root is permitted to execute the reviewed project overlay; no target first-boot installer.
xz -t "$source_image"
xz -dc "$source_image" > "$image"
sfdisk --json "$image" > "$build_dir/partition.json"
read -r start < <(python3 - "$build_dir/partition.json" <<'PY'
import json, sys
t = json.load(open(sys.argv[1]))['partitiontable']
assert t['label'] == 'dos' and t['sectorsize'] == 512
assert len(t['partitions']) == 1 and t['partitions'][0]['type'] == '83'
p = t['partitions'][0]
assert p['start'] == 8192, 'Unexpected Armbian boot layout'
print(p['start'])
PY
)
dd if="$image" of="$build_dir/boot-prefix" bs=512 count="$start" status=none
truncate -s 4G "$image"
new_size=$(( 4 * 1024 * 1024 * 1024 / 512 - start ))
printf 'start=%s, size=%s, type=83\n' "$start" "$new_size" | sfdisk --no-reread --force -N 1 "$image"
# sfdisk may only change the partition records. SPL/U-Boot and disk ID must survive.
python3 - "$image" "$build_dir/boot-prefix" <<'PY'
from pathlib import Path
import sys
old = Path(sys.argv[2]).read_bytes()
with open(sys.argv[1], 'rb') as f:
    new = f.read(len(old))
assert new[:446] == old[:446] and new[510:] == old[510:], 'Boot bytes changed outside partition table'
PY
loop=$(losetup --find --show --partscan "$image")
part=${loop}p1
[[ -b $part ]] || { echo "Partition device unavailable: $part" >&2; exit 1; }
fsck_status=0
e2fsck -fy "$part" || fsck_status=$?
[[ $fsck_status -le 1 ]] || exit "$fsck_status"
resize2fs "$part"
mkdir -p "$rootfs"
mount "$part" "$rootfs"
rg_board=$(sed -n 's/^BOARD=//p' "$rootfs/etc/armbian-release")
[[ $rg_board == orangepizero3 ]] || { echo 'This is not an Orange Pi Zero 3 image.' >&2; exit 1; }
# Require the active kernel's built-in hardware watchdog and read-only status interface.
kernel_image=$(readlink -- "$rootfs/boot/Image")
[[ $kernel_image == vmlinuz-* && $kernel_image != */* ]] || {
  echo 'Unexpected boot Image symlink; review the active kernel before enabling the watchdog.' >&2; exit 1;
}
kernel_config="$rootfs/boot/config-${kernel_image#vmlinuz-}"
for option in WATCHDOG WATCHDOG_SYSFS SUNXI_WATCHDOG; do
  grep -qx "CONFIG_${option}=y" "$kernel_config" || {
    echo "The active kernel must provide built-in CONFIG_${option}=y." >&2; exit 1;
  }
done
}
