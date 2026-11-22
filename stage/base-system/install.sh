#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
apt update
apt -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y --with-new-pkgs
install_declared_packages
systemctl mask apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
