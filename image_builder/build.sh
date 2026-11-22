#!/bin/bash
# Run on a networked Debian/Ubuntu Linux builder with root, loop/mount and ARM64 binfmt.
set -euo pipefail
umask 022
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
project=$PWD
source image_builder/plan.sh

usage() {
  cat <<'HELP'
Usage: sudo bash build.sh BASE_IMAGE.img.xz VERSION OTA_CERT.pem
       bash build.sh --list-stages
       bash build.sh --help

Build the fully preinstalled ARM64 factory image and OTA root payload.
Stages run in order; failed builds retain work/build.*/logs and stages.tsv.
The OTA certificate is public. Bundle signing is a separate packaging step.
HELP
}
case ${1:-} in
  --list-stages) [[ $# == 1 ]] || { usage >&2; exit 2; }; list_build_stages; exit 0 ;;
  --help|-h) usage; exit 0 ;;
esac
[[ $# == 3 ]] || { usage >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'Run the image build as root on a networked Linux builder.' >&2; exit 1; }
source_image=$1 version=$2 certificate=$3
for cmd in flock mktemp mkdir tee; do
  command -v "$cmd" >/dev/null || { echo "Missing build runner tool: $cmd" >&2; exit 1; }
done
source image_builder/runtime.sh
initialize_build
position=0
for entry in "${BUILD_STAGES[@]}"; do
  IFS='|' read -r id context title description script <<< "$entry"
  position=$((position + 1))
  run_build_stage "$id" "$context" "$title" "$description" "$position" "${#BUILD_STAGES[@]}" "$script"
done
