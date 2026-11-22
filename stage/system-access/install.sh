#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
install_declared_packages
# A fixed key-only ordinary identity; only root gets a recovery password.
if ! getent passwd pi >/dev/null; then
  ! getent passwd 1000 >/dev/null || { echo 'UID 1000 is already occupied; review the base image.' >&2; exit 1; }
  if ! getent group pi >/dev/null; then groupadd --gid 1000 pi; fi
  [[ $(getent group pi | cut -d: -f3) == 1000 ]] || exit 1
  useradd --uid 1000 --gid pi --create-home --shell /bin/bash --password '!' pi
fi
[[ $(id -u pi) == 1000 && $(getent passwd pi | cut -d: -f6) == /home/pi ]] || exit 1
usermod --password '!' pi
usermod --password '!' root
install -m 440 /usr/share/network-exit/sudoers /etc/sudoers
install -d -m 700 /etc/enodectl-sudoers.d
rm -f /etc/enodectl-sudoers.d/99-bootstrap
chmod 755 /usr/lib/network-exit/console-bootstrap /usr/lib/network-exit/login-bootstrap
# Debian login shells must load our automatic first-boot entry.
grep -q '/etc/profile.d' /etc/profile
# The node wizard owns initial passwords/accounts, not Armbian's root-login wizard.
rm -f /root/.not_logged_in_yet /root/.not_logged_in_yet_lock
# Ordinary root login is disabled; root recovery via sudo remains available.
sed -i '1i auth requisite pam_succeed_if.so user != root quiet' /etc/pam.d/login
python3 - <<'SSH_POLICY'
from pathlib import Path
path=Path('/etc/ssh/sshd_config')
policy='Include /etc/ssh/sshd_config.d/00-enodectl-access.conf\n'
path.write_text(policy+path.read_text())
SSH_POLICY
visudo -c
systemctl enable ssh.service

# Never ship the base image's SSH host identity.
rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
