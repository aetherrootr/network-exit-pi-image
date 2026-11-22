#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
case $(uname -m) in
  aarch64|arm64) ;;
  x86_64)
    qemu=$(command -v qemu-aarch64-static || command -v qemu-aarch64 || true)
    [[ -n $qemu ]] || { echo 'Install qemu-user-static and ARM64 binfmt on the builder first.' >&2; exit 1; }
    qemu_added=1
    cp -L "$qemu" "$rootfs/usr/bin/network-exit-build-qemu-aarch64"
    ;;
  *) echo 'The builder must be ARM64 or x86_64 with ARM64 binfmt.' >&2; exit 1;;
esac
# This also verifies recursive ARM64 execution (maintainer scripts need binfmt).
if ! chroot "$rootfs" /bin/sh -c '/usr/bin/dpkg --print-architecture' | grep -qx arm64; then
  echo 'ARM64 binfmt is not enabled. Install qemu-user-static/binfmt-support and enable qemu-aarch64, or use an ARM64 builder.' >&2
  exit 1
fi
cp -a "$rootfs/etc/resolv.conf" "$build_dir/resolv.conf"
resolver_saved=1
rm -f "$rootfs/etc/resolv.conf"
cp -L /etc/resolv.conf "$rootfs/etc/resolv.conf"
if [[ -e $rootfs/usr/sbin/policy-rc.d ]]; then
  cp -a "$rootfs/usr/sbin/policy-rc.d" "$build_dir/policy-rc.d"
  policy_saved=1
fi
policy_written=1
printf '#!/bin/sh\nexit 101\n' > "$rootfs/usr/sbin/policy-rc.d"
chmod 755 "$rootfs/usr/sbin/policy-rc.d"
mount --rbind /dev "$rootfs/dev"
mount --make-rslave "$rootfs/dev"
mount -t proc proc "$rootfs/proc"
mount -t sysfs sysfs "$rootfs/sys"
mount -t tmpfs tmpfs "$rootfs/run"
}
