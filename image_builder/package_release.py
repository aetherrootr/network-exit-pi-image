#!/usr/bin/env python3
"""Produce a factory flash archive and a separately signed slot-only OTA bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from release.lib import factory

ASSET_LIMIT = 2 * 1024 ** 3  # GitHub Release assets must be strictly below 2 GiB.


def validate_asset_size(path, limit=ASSET_LIMIT):
    size = Path(path).stat().st_size
    if size >= limit:
        raise ValueError(f'Release asset {Path(path).name} is {size} bytes; must be smaller than {limit} bytes')


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def checksum_file(path):
    path.with_name(path.name + '.sha256').write_text(f'{digest(path)}  {path.name}\n')


def package(root, version, certificate=None, key=None, factory_only=False):
    root = Path(root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', version):
        raise ValueError('Invalid image version')
    built = json.loads((root / 'output/build.json').read_text())
    if built.get('version') != version or built.get('layout') != 'ab-v2':
        raise ValueError('Build metadata does not match this version and A/B layout')
    if not factory_only and (not certificate or not key):
        raise ValueError('Both OTA signing certificate and private key are required')
    if not factory_only and digest(certificate) != built.get('ota_certificate_sha256'):
        raise ValueError('Signing certificate differs from the trust certificate built into this image')
    components = root / 'output/factory'
    manifest = json.loads((components / 'layout.json').read_text())
    factory.verify(components, manifest)
    # Factory and OTA must contain exactly the same built root payload.
    if not factory_only and digest(root / 'output/rootfs.img') != manifest['payloads']['rootfs']['raw_sha256']:
        raise ValueError('Factory and OTA rootfs payloads differ')
    name = f'network-exit-factory-{version}-orangepizero3'
    staging = root / 'output/bundle' / name
    if staging.exists(): raise ValueError('Factory package staging directory already exists')
    staging.mkdir(parents=True)
    shutil.copytree(root / 'release', staging, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(root / 'stage/system-access/enodectl_component/sshpub.py', staging / 'lib/sshpub.py')
    (staging / 'burn.sh').chmod(0o755)
    shutil.copytree(components / 'payload', staging / 'payload')
    for src in [root / 'README.md', root / 'LICENSE', root / 'output/package-manifest.tsv',
                root / 'output/enodectl-manifest.json',
                root / 'output/source-image.sha256', root / 'output/build.json']:
        shutil.copy2(src, staging / src.name)
    manifest['version'] = version
    (staging / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n')
    checksums = [f'{digest(path)}  {path.relative_to(staging)}\n'
                 for path in sorted(staging.rglob('*')) if path.is_file()]
    (staging / 'SHA256SUMS').write_text(''.join(checksums))
    archive = root / 'output' / f'{name}.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=1) as stream: stream.add(staging, arcname=name)
    validate_asset_size(archive)
    checksum_file(archive)
    products = [archive]
    if not factory_only:
        ota = root / 'output' / f'network-exit-ota-{version}-orangepizero3.raucb'
        if ota.exists(): raise ValueError('OTA output already exists')
        with tempfile.TemporaryDirectory(dir=root / 'output') as tmp:
            inputs = Path(tmp) / 'input'
            inputs.mkdir()
            shutil.copy2(root / 'output/rootfs.img', inputs / 'rootfs.img')
            (inputs / 'manifest.raucm').write_text(
                f'[update]\ncompatible=network-exit-orangepizero3-v2\nversion={version}\n\n'
                '[bundle]\nformat=plain\n\n[image.rootfs]\nfilename=rootfs.img\n')
            # plain is explicit because the pinned kernel lacks CONFIG_DM_VERITY.
            # Bundles remain signed; the device uses private staged files for install.
            subprocess.run(['rauc', 'bundle', '--mksquashfs-args=-processors 2', f'--cert={Path(certificate).resolve()}',
                            f'--key={Path(key).resolve()}', str(inputs), str(ota)], check=True)
        subprocess.run(['rauc', 'info', f'--keyring={Path(certificate).resolve()}', str(ota)], check=True)
        validate_asset_size(ota)
        checksum_file(ota)
        products.append(ota)
    return products


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--certificate')
    parser.add_argument('--key')
    parser.add_argument('--factory-only', action='store_true', help='Development packaging check without an OTA bundle')
    args = parser.parse_args()
    try:
        for product in package(Path(__file__).resolve().parents[1], args.version, args.certificate, args.key, args.factory_only):
            print(f'{product} ({product.stat().st_size} bytes)')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Packaging failed: {exc}\n')


if __name__ == '__main__': main()
