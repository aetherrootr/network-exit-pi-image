#!/bin/bash
set -euo pipefail
[[ -n ${ENODECTL_TARGET_ROOT:-} && -n ${ENODECTL_BUILD_DIR:-} && -n ${ENODECTL_PROJECT:-} ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
cd -- "$ENODECTL_PROJECT"
python3 -m venv "$ENODECTL_BUILD_DIR/python-tools"
"$ENODECTL_BUILD_DIR/python-tools/bin/python" -m pip install --no-cache-dir --disable-pip-version-check -r stage/enodectl/build-requirements.txt
python_version=$(chroot "$ENODECTL_TARGET_ROOT" /usr/bin/python3 -c 'import sys; print("%s.%s" % sys.version_info[:2])')
"$ENODECTL_BUILD_DIR/python-tools/bin/python" -m image_builder.package_enodectl --target "$ENODECTL_TARGET_ROOT/usr/lib/enodectl/enodectl.pyz" --python-version "$python_version"
python3 - "$ENODECTL_TARGET_ROOT" "${ENODECTL_IMAGE_VERSION:?}" <<'RELEASE'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);folder=root/'usr/share/network-exit';folder.mkdir(parents=True,exist_ok=True)
(folder/'release.json').write_text(json.dumps({'version':sys.argv[2], 'compatible':'network-exit-orangepizero3-v2','layout':'ab-v2'})+'\n')
RELEASE
install -m 644 "${ENODECTL_OTA_CERTIFICATE:?}" "$ENODECTL_TARGET_ROOT/usr/share/network-exit/ota-ca.pem"
install -m 644 README.md LICENSE "$ENODECTL_TARGET_ROOT/usr/share/network-exit/"
