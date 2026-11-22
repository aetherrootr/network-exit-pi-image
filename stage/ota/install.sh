#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install_declared_packages
install -D -m 644 config/update-source.json /usr/share/network-exit/update-source.json
# Debian's rauc package does not create this local-configuration directory.
install -d -m 755 /etc/rauc
ln -sfn /run/rauc/system.conf /etc/rauc/system.conf
systemctl enable network-exit-confirm.timer network-exit-trial-deadline.timer
