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
# Armbian also checks for package upgrades from cron independently of APT timers.
# Its simulated upgrades filled the 122 MiB RAM overlay on the 1 GiB Zero 3.
rm -f /etc/cron.d/armbian-updates
# The deployed appliance uses signed full-image updates and a bounded RAM upper.
cat > /etc/apt/apt.conf.d/99-enode-no-disk-cache <<'APT_CACHE'
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
APT_CACHE
