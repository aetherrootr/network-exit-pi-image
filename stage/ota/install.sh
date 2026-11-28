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
# Debian splits the installer daemon and D-Bus activation files from its CLI.
# Keep the service on demand; the STATE service orders its runtime config first.
test -f /usr/lib/systemd/system/rauc.service
test -f /usr/share/dbus-1/system-services/de.pengutronix.rauc.service
test -f /usr/share/dbus-1/system.d/de.pengutronix.rauc.conf
systemctl disable rauc.service
systemctl enable network-exit-confirm.timer network-exit-trial-deadline.timer
