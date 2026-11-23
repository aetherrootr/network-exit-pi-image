#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
# Store one compressed rootfs, mapped to both independent system partitions.
python3 -m image_builder.assemble_factory --compress "$factory" --rootfs "$slot_image"
}
