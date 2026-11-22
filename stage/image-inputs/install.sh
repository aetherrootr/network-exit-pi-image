#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
[[ -f $certificate ]] || { echo 'OTA signing certificate not found.' >&2; exit 1; }
certificate=$(readlink -f -- "$certificate")
[[ $version =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo 'Invalid version.' >&2; exit 1; }
for cmd in xz sfdisk losetup mount mountpoint umount e2fsck resize2fs chroot python3 curl sha256sum mkimage mkfs.ext4 openssl dd truncate cp rm install sed grep readlink uname cmp sync; do
  command -v "$cmd" >/dev/null || { echo "Missing build tool: $cmd" >&2; exit 1; }
done
source_image=$(readlink -f -- "$source_image")
[[ -f $source_image ]] || { echo "Base image not found: $source_image" >&2; exit 1; }
python3 - "$source_image" stage/image-inputs/config/base-image.json <<'PY'
import hashlib, json, sys
with open(sys.argv[1], 'rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
assert digest == json.load(open(sys.argv[2]))['sha256'], 'Base image SHA256 mismatch'
PY
openssl x509 -in "$certificate" -noout
}
