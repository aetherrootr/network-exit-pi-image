#!/bin/bash
# Called only by the image build runner.
set -euo pipefail
[[ ${ENODECTL_BUILD_CHROOT:-} == 1 && -f /etc/armbian-release ]] || {
  echo 'This stage must run inside the image through build.sh.' >&2
  exit 2
}
source "${ENODECTL_STAGE_ROOT:?}/common.sh"
# The builder mounts a fresh /run, including when openssh-server was already
# installed in the base image. sshd -T still requires its privilege-separation dir.
install -d -m 755 /run/sshd
ssh-keygen -q -t ed25519 -N '' -f /run/enodectl-ssh-policy-test
/usr/sbin/sshd -T -h /run/enodectl-ssh-policy-test -C user=root,host=localhost,addr=127.0.0.1 | grep -qx 'permitrootlogin no'
/usr/sbin/sshd -T -h /run/enodectl-ssh-policy-test -C user=pi,host=localhost,addr=127.0.0.1 > /run/enodectl-ssh-policy.txt
for expected in 'allowusers pi' 'denyusers root' 'pubkeyauthentication yes' 'authenticationmethods publickey' \
    'passwordauthentication no' 'kbdinteractiveauthentication no' 'permitemptypasswords no' 'usepam yes' 'trustedusercakeys /etc/network-exit/ssh-user-ca.pub' \
    'authorizedprincipalsfile none' 'authorizedprincipalscommand none'; do
  grep -qx "$expected" /run/enodectl-ssh-policy.txt
done
python3 - /run/enodectl-ssh-policy.txt <<'SSH_SK'
from pathlib import Path
import sys
policy=dict(line.split(' ',1) for line in Path(sys.argv[1]).read_text().splitlines())
for algorithm in ('sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com'):
    assert algorithm in policy['casignaturealgorithms'].split(','), 'Security-key CA signatures disabled'
    assert algorithm in policy['pubkeyacceptedalgorithms'].split(','), 'Security-key authentication disabled'
SSH_SK
rm -f /run/enodectl-ssh-policy-test /run/enodectl-ssh-policy-test.pub /run/enodectl-ssh-policy.txt
for config in /boot/config-*; do
  kernel=${config##*/config-}
  update-initramfs -u -k "$kernel"
  # Execute the tools from the actual generated ARM64 archive. A file listing
  # alone misses missing applets, broken symlinks and unsupported shell syntax.
  initramfs_dir=$(mktemp -d /run/enodectl-initramfs.XXXXXX)
  trap 'rm -rf "$initramfs_dir"' EXIT
  unmkinitramfs "/boot/initrd.img-$kernel" "$initramfs_dir"
  initramfs_root=$initramfs_dir
  if [[ -d $initramfs_dir/main ]]; then initramfs_root=$initramfs_dir/main; fi
  chroot "$initramfs_root" /bin/sh -e <<'VERIFY_INITRAMFS'
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    test "$(/bin/sh -c 'trap "echo ok" EXIT')" = ok
    for utility in busybox grep sed awk chmod chown blkid mount modprobe cp mkdir mv tr touch; do
      command -v "$utility" || { echo "Missing initramfs utility: $utility" >&2; exit 1; }
    done
    printf "nodev\toverlay\n" | grep -q "$(printf "\t")overlay$"
    printf "test\n" | grep -Eq "^test$"
    test -r /lib/network-exit/overlay-limit.sh
    test -x /scripts/init-bottom/overlayroot
    test -x /scripts/init-bottom/zz-network-exit-state
    test -x /scripts/init-bottom/zzz-enode-connectivity
    touch /enodectl-verify-reference /enodectl-verify-target
    chmod --reference=/enodectl-verify-reference /enodectl-verify-target
    chown --reference=/enodectl-verify-reference /enodectl-verify-target
    echo 'Initramfs shell and utilities verified'
VERIFY_INITRAMFS
  rm -rf "$initramfs_dir"
  trap - EXIT
done
sh -n /usr/lib/network-exit/overlay-limit.sh
sh -n /etc/initramfs-tools/hooks/network-exit
sh -n /etc/initramfs-tools/scripts/init-bottom/zz-network-exit-state
python3 -c 'import asyncio, ssl'
/usr/local/sbin/enodectl --help
/usr/local/sbin/enodectl config --help
sh -n /etc/profile.d/90-enodectl-firstboot.sh
bash -n /usr/lib/network-exit/node-watchdog
/usr/local/sbin/enodectl --internal verify-installed
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /usr/share/network-exit/package-manifest.tsv
