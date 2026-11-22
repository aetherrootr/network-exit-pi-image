#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install -d -m 755 /usr/share/keyrings
fetch https://pkg.cloudflare.com/cloudflare-main.gpg /usr/share/keyrings/cloudflare-main.gpg
chmod 644 /usr/share/keyrings/cloudflare-main.gpg
apt update
install_declared_packages
systemctl disable cloudflared.service network-exit-cloudflared.service 2>/dev/null || true
