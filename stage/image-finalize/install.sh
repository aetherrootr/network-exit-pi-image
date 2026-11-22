#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
restore_build_files
cp "$rootfs/usr/share/network-exit/package-manifest.tsv" "$build_dir/package-manifest.tsv"
cp "$rootfs/usr/lib/enodectl/enodectl.json" "$build_dir/enodectl-manifest.json"
sync
unmount_target
fsck_status=0
e2fsck -fn "$part" || fsck_status=$?
[[ $fsck_status == 0 ]] || exit "$fsck_status"
detach_loop
}
