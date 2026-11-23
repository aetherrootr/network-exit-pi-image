#!/bin/bash
# Sourced only by the image build runner; retain shared resource state.
[[ ${ENODECTL_BUILD_HOST_SHELL:-} == 1 ]] || {
  echo 'This stage must run inside the image build through build.sh.' >&2
  exit 2
}
# Sourced by the build runner; shared variables describe the working image.
build_stage() {
(cd -- "$(dirname -- "$source_image")" && sha256sum "$(basename -- "$source_image")") > "$build_dir/source-image.sha256"
python3 - "$version" "$certificate" "$build_dir" <<'BUILD'
import hashlib,json,sys
from pathlib import Path
(Path(sys.argv[3])/'build.json').write_text(json.dumps({'version':sys.argv[1], 'layout':'ab-v2', 'compatible':'network-exit-orangepizero3-v2', 'hardware_validated':False, 'ota_certificate_sha256':hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()}, indent=2)+'\n')
BUILD
python3 - "$factory" "$build_dir/SHA256SUMS" <<'CHECKSUM'
import hashlib,sys
from pathlib import Path
root=Path(sys.argv[1])
lines=[]
for path in sorted(root.rglob('*')):
    if path.is_file():
        with path.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        lines.append(f'{digest}  factory/{path.relative_to(root)}\n')
Path(sys.argv[2]).write_text(''.join(lines))
CHECKSUM
for name in package-manifest.tsv enodectl-manifest.json source-image.sha256 build.json SHA256SUMS; do
  mv "$build_dir/$name" "$project/output/$name"
done
mv "$slot_image" "$project/output/rootfs.img"
# Publishing the component directory last prevents packaging an incomplete build.
mv "$factory" "$final"
}
