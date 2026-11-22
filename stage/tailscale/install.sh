#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install -d -m 755 /usr/share/keyrings /etc/apt/sources.list.d
fetch https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg /usr/share/keyrings/tailscale-archive-keyring.gpg
fetch https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list /etc/apt/sources.list.d/tailscale.list
chmod 644 /usr/share/keyrings/tailscale-archive-keyring.gpg
apt update
install_declared_packages
systemctl enable tailscaled.service
