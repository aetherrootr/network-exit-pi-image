#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
# Both root slots include their matching kernel, DTB, initramfs and boot script.
dd if="$image" of="$slot_image" bs=1M skip=4 status=none conv=sparse
# Use the original verified base prefix captured before any package upgrades.
python3 -m image_builder.assemble_factory --base "$build_dir/boot-prefix" --rootfs "$slot_image" --output "$factory" --components
}
