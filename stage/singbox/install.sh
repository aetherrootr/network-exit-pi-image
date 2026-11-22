#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install -d -m 755 /etc/apt/keyrings
fetch https://sing-box.app/gpg.key /etc/apt/keyrings/sagernet.asc
chmod 644 /etc/apt/keyrings/sagernet.asc
apt update
install_declared_packages
systemctl disable sing-box.service network-exit-singbox.service 2>/dev/null || true
