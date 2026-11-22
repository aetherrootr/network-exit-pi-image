#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install_declared_packages
systemctl disable ddclient.service network-exit-ddns.timer 2>/dev/null || true
systemctl mask ddclient.service
rm -f /etc/cron.d/ddclient /etc/cron.daily/ddclient
rm -f /etc/dhcp/dhclient-exit-hooks.d/ddclient /etc/ppp/ip-up.d/ddclient
rm -f /etc/ddclient.conf /etc/ddclient/ddclient.conf
