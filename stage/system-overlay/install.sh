#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install_declared_packages
printf '%s\n' 'overlayroot="tmpfs:recurse=0"' > /etc/overlayroot.conf
chmod 755 /etc/initramfs-tools/hooks/network-exit /usr/lib/network-exit/overlay-limit.sh
