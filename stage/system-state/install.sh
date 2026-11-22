#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install_declared_packages
chmod 755 /etc/initramfs-tools/scripts/init-bottom/zz-network-exit-state
systemctl mask armbian-resize-filesystem.service armbian-firstrun.service
systemctl enable network-exit-state.service
