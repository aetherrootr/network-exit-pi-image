#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install_declared_packages
chmod 600 /etc/netplan/10-dhcp-all-interfaces.yaml
chmod 755 /etc/initramfs-tools/scripts/init-bottom/zzz-enode-connectivity
install -d -m 700 /etc/network-exit/connectivity
install -m 600 /usr/share/network-exit/wifi-default-profile.json /etc/network-exit/connectivity/profile.json
install -m 600 /usr/share/network-exit/wifi-default-netplan.yaml /etc/network-exit/connectivity/netplan.yaml
ln -sfn /etc/network-exit/connectivity/netplan.yaml /etc/netplan/90-enode-wifi.yaml
if grep -q '^127\.0\.1\.1' /etc/hosts; then
  sed -i 's/^127\.0\.1\.1.*/127.0.1.1 enode/' /etc/hosts
else
  printf '\n127.0.1.1 enode\n' >> /etc/hosts
fi
netplan generate
systemctl disable nftables.service 2>/dev/null || true
systemctl enable systemd-networkd.service systemd-resolved.service network-exit-network.service network-exit-uplink-refresh.timer
