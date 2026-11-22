#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
# Divert vendor frontends only inside this mounted image. Keep systemctl's real
# implementation for service management and the controller's private reboot API.
test -f /usr/bin/systemctl
test ! -L /usr/bin/systemctl
dpkg-divert --local --add --rename --divert /usr/lib/network-exit/systemctl.real /usr/bin/systemctl
install -m 755 /usr/lib/network-exit/systemctl-guard /usr/bin/systemctl
dpkg-divert --local --add --rename --divert /usr/lib/network-exit/su.real /usr/bin/su
chmod 700 /usr/lib/network-exit/su.real
install -m 755 /usr/lib/network-exit/su-guard /usr/bin/su
install -d -m 700 /usr/lib/network-exit/vendor-power
for directory in /usr/bin /usr/sbin; do
  for name in reboot poweroff halt shutdown telinit; do
    vendor="/usr/lib/network-exit/vendor-power/$(basename "$directory")-$name"
    dpkg-divert --local --add --rename --divert "$vendor" "$directory/$name"
    install -m 755 /usr/lib/network-exit/power-command-guard "$directory/$name"
  done
done
/usr/lib/network-exit/systemctl.real --root=/ mask poweroff.target halt.target kexec.target \
  suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target ctrl-alt-del.target
cmp /usr/lib/network-exit/power-command-guard /usr/sbin/reboot
cmp /usr/lib/network-exit/power-command-guard /usr/sbin/poweroff
cmp /usr/lib/network-exit/systemctl-guard /usr/bin/systemctl
/usr/local/sbin/enodectl --internal check-power-policy
apt-get clean
# No service has run, so identities and credentials remain per-device first-boot work.
test ! -s /var/lib/tailscale/tailscaled.state
