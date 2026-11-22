#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
# Keep the compressed image private to this build until all manifests are ready.
xz -T2 -3 -c "$factory" > "$compressed_image"
xz -t "$compressed_image"
}
