#!/usr/bin/env python3
"""Download a fresh pinned upstream image into a caller-owned directory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def fetch_image(config, output_dir):
    name = config['filename']
    if Path(name).name != name or not name.endswith('.img.xz'):
        raise ValueError('Invalid base image filename')
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    # Always fetch, even when a previous file exists. Publish only verified bytes.
    with tempfile.TemporaryDirectory(prefix='.download-', dir=output_dir) as tmp:
        partial = Path(tmp) / name
        subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                        '--retry', '3', '--connect-timeout', '20', config['url'],
                        '-o', str(partial)], check=True)
        with partial.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != config['sha256']:
            raise ValueError('Upstream image SHA256 mismatch; refusing to build.')
        partial.replace(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True,
                        help='Temporary directory for this build; the caller must clean it up afterward')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        config = json.loads((root / 'stage/image-inputs/config/base-image.json').read_text())
        print(fetch_image(config, args.output_dir))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Download failed: {exc}\n')


if __name__ == '__main__':
    main()
