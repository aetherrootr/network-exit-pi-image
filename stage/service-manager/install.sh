#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
# Runtime menus, orchestration and service registrations are bundled by the enodectl stage.
