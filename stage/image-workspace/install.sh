#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
mkdir -p "$project/output"
[[ ! -e $final ]] || { echo "Output already exists; move it before building: $final" >&2; exit 1; }
echo "Working directory: $build_dir"
echo "Final image: $final"
}
