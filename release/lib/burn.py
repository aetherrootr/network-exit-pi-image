#!/usr/bin/env python3
"""Linux whole-device SD writer. No device writes occur before explicit confirmation."""
import argparse
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import stat
import shutil
import struct
import subprocess
import sys
import time
import tempfile

CHUNK = 4 * 1024 * 1024

_spec = importlib.util.spec_from_file_location('factory_transport', Path(__file__).with_name('factory.py'))
factory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(factory)


def read_ssh_keys(path):
    for command in ['ssh-keygen', 'losetup', 'debugfs', 'e2fsck']:
        if not shutil.which(command):
            raise ValueError(f'--ssh-key requires {command} on the Linux writer')
    helper = Path(__file__).with_name('sshpub.py')
    if not helper.exists():
        helper = Path(__file__).resolve().parents[2] / 'stage/system-access/enodectl_component/sshpub.py'
    spec = importlib.util.spec_from_file_location('flash_sshpub', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Path(path).open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('--ssh-key must name a regular public-key file')
        data = stream.read(module.MAX_SOURCE + 1)
    if len(data) > module.MAX_SOURCE:
        raise ValueError('Public-key file exceeds 64 KiB')
    keys = module.validated_keys(data.decode('utf-8'))
    return ''.join(line + '\n' for line, _ in keys.values())


def personalize_state(volume, keys):
    """Offline ext4 operation; regular-file fixtures test this without a card."""
    with tempfile.TemporaryDirectory(prefix='enodectl-pi-key-', dir='/tmp') as tmp:
        public = Path(tmp) / 'authorized_keys'
        public.write_text(keys, encoding='utf-8')
        public.chmod(0o600)
        environment = {**os.environ, 'LC_ALL': 'C'}
        commands = ['mkdir /homes', 'mkdir /homes/pi', 'mkdir /homes/pi/.ssh',
                    f'write {public} /homes/pi/.ssh/authorized_keys']
        for name, mode in [('/homes/pi', '040755'), ('/homes/pi/.ssh', '040700'),
                           ('/homes/pi/.ssh/authorized_keys', '0100600')]:
            commands += [f'set_inode_field {name} uid 1000', f'set_inode_field {name} gid 1000',
                         f'set_inode_field {name} mode {mode}']
        for command in commands:
            subprocess.run(['debugfs', '-w', '-R', command, str(volume)], check=True, capture_output=True, env=environment)
        result = subprocess.run(['debugfs', '-R', 'cat /homes/pi/.ssh/authorized_keys', str(volume)],
                                check=True, capture_output=True, text=True, encoding='utf-8', env=environment)
        if result.stdout != keys:
            raise ValueError('SSH key readback failed; do not deploy this card')
        # debugfs may report filesystem errors without a nonzero process status.
        for name, mode in [('/homes/pi', '0755'), ('/homes/pi/.ssh', '0700'),
                           ('/homes/pi/.ssh/authorized_keys', '0600')]:
            info = subprocess.run(['debugfs', '-R', f'stat {name}', str(volume)],
                                  check=True, capture_output=True, text=True, encoding='utf-8', env=environment).stdout
            import re
            if not re.search(r'User:\s+1000\s+Group:\s+1000', info) or not re.search(r'Mode:\s+' + mode + r'\b', info):
                raise ValueError('SSH key ownership/permissions verification failed')
        subprocess.run(['e2fsck', '-fn', str(volume)], check=True, capture_output=True, env=environment)


def provision_ssh_key(device, manifest, keys):
    if manifest.get('layout') != 'ab-v2':
        raise ValueError('SSH key provisioning requires the ab-v2 factory layout')
    inspect_device(device, manifest['image_bytes'])
    with open(device, 'rb', buffering=0) as stream:
        mbr = stream.read(512)
    if len(mbr) != 512 or mbr[510:] != b'\x55\xaa' or mbr[498] != 0x83:
        raise ValueError('Cannot identify the factory STATE partition')
    start, sectors = struct.unpack_from('<II', mbr, 502)
    expected_start = 8192 + 2 * 4092 * 2048 + 2048
    if start != expected_start or sectors < 2048 * 2048 or (start + sectors) * 512 > manifest['image_bytes']:
        raise ValueError('Unexpected STATE geometry; refusing SSH provisioning')
    loop = subprocess.check_output(['losetup', '--find', '--show', '--offset', str(start * 512),
                                    '--sizelimit', str(sectors * 512), device], text=True).strip()
    try:
        personalize_state(loop, keys)
        descriptor = os.open(loop, os.O_RDWR | os.O_NOFOLLOW)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        subprocess.run(['losetup', '--detach', loop], check=True)
    print('Per-device SSH public keys saved for pi. First SSH login: ssh pi@DEVICE_ADDRESS')


def read_manifest(directory):
    manifest = json.loads((directory / 'release.json').read_text())
    if not isinstance(manifest, dict):
        raise ValueError('Invalid release.json')
    if manifest.get('format') == 2:
        factory.validate(directory, manifest)
        return manifest, directory
    name = manifest['image']
    if manifest.get('format') != 1 or Path(name).name != name or not name.endswith('.img.xz'):
        raise ValueError('Invalid release.json')
    if not isinstance(manifest['image_bytes'], int) or manifest['image_bytes'] < 512:
        raise ValueError('Invalid image size')
    for field in ['sha256', 'image_sha256']:
        if len(manifest[field]) != 64 or any(c not in '0123456789abcdef' for c in manifest[field]):
            raise ValueError('Invalid image checksum')
    return manifest, directory / name


def verify_image(image, manifest):
    if manifest.get('format') == 2:
        print('Verifying shared factory payloads and partition map...', flush=True)
        factory.verify(image, manifest)
        return
    print('Verifying compressed image and decompressed data...', flush=True)
    with image.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != manifest['sha256']:
            raise ValueError('Compressed image SHA256 mismatch')
    digest = hashlib.sha256()
    count = 0
    with lzma.open(image, 'rb') as stream:
        while chunk := stream.read(CHUNK):
            count += len(chunk)
            if count > manifest['image_bytes']:
                raise ValueError('Decompressed image exceeds the declared size')
            digest.update(chunk)
    if count != manifest['image_bytes'] or digest.hexdigest() != manifest['image_sha256']:
        raise ValueError('Decompressed image size or SHA256 mismatch')


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get('children', []))


def validate_disk(node, needed):
    if node['type'] != 'disk':
        raise ValueError('Specify a whole SD card device, not a partition, loop device or mapped device')
    if node.get('ro'):
        raise ValueError('Target device is read-only')
    if int(node['size']) < needed:
        raise ValueError('Target device is smaller than the image')
    for child in walk([node]):
        mounts = [p for p in (child.get('mountpoints') or []) if p]
        if mounts:
            raise ValueError(f"{child['name']} is in use: {', '.join(mounts)}; unmount it first. Never flash a system disk or active swap device")


def inspect_device(device, needed):
    if not stat.S_ISBLK(os.stat(device).st_mode):
        raise ValueError('Target must be a block device under /dev, not a regular file')
    data = subprocess.check_output(['lsblk', '--json', '--bytes', '--paths',
        '--output', 'NAME,TYPE,SIZE,RO,MODEL,MOUNTPOINTS', device], text=True)
    matches = [node for node in walk(json.loads(data)['blockdevices']) if node['name'] == device]
    if not matches:
        raise ValueError('Cannot identify the target device')
    node = matches[0]
    validate_disk(node, needed)
    return node


def write_image(image, device, manifest):
    if manifest.get('format') == 2:
        return write_components(image, device, manifest)
    # O_EXCL on a block device fails if it is in use (e.g. a mounted partition).
    descriptor = os.open(device, os.O_WRONLY | os.O_EXCL | os.O_NOFOLLOW)
    try:
        written = 0
        digest = hashlib.sha256()
        last_report = 0
        with lzma.open(image, 'rb') as source:
            while chunk := source.read(CHUNK):
                if written + len(chunk) > manifest['image_bytes']:
                    raise ValueError('Image size changed; stopping the write')
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    count = os.write(descriptor, view)
                    if count <= 0:
                        raise OSError('Device write failed')
                    view = view[count:]
                written += len(chunk)
                if time.monotonic() - last_report >= 1:
                    print(f'\rWritten {written / 1048576:.0f} / {manifest["image_bytes"] / 1048576:.0f} MiB', end='', flush=True)
                    last_report = time.monotonic()
        os.fsync(descriptor)
        print()
        if written != manifest['image_bytes'] or digest.hexdigest() != manifest['image_sha256']:
            raise ValueError('Image data changed during writing; do not use this card')
    finally:
        os.close(descriptor)
    print('Reading back the SD card for verification...', flush=True)
    digest = hashlib.sha256()
    remaining = manifest['image_bytes']
    with open(device, 'rb', buffering=0) as target:
        while remaining:
            chunk = target.read(min(CHUNK, remaining))
            if not chunk:
                raise OSError('Device readback ended unexpectedly')
            remaining -= len(chunk)
            digest.update(chunk)
    if digest.hexdigest() != manifest['image_sha256']:
        raise ValueError('SD card readback verification failed; check the card reader and card')


def write_components(directory, device, manifest):
    writes = factory.validate(directory, manifest)
    # Keep an exclusive descriptor through both writing and readback. Each map
    # entry is contiguous; no sparse holes or stale bytes are skipped on a card.
    descriptor = os.open(device, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW)
    try:
        total, last_report = 0, 0
        for write in writes:
            item = manifest['payloads'][write['payload']]
            print(f'Writing {write["payload"]} at byte {write["offset"]}...', flush=True)
            os.lseek(descriptor, write['offset'], os.SEEK_SET)
            count = 0
            for chunk in factory.chunks(directory, item):
                # Bind the on-card partition records to the checked write map,
                # including when package contents changed after preflight.
                if write['payload'] == 'boot-prefix' and count == 0:
                    factory.verify_mbr(chunk, manifest)
                view = memoryview(chunk)
                while view:
                    amount = os.write(descriptor, view)
                    if amount <= 0:
                        raise OSError('Device write failed')
                    view = view[amount:]
                count += len(chunk)
                if time.monotonic() - last_report >= 1:
                    position = write['offset'] + count
                    print(f'\rWritten {position / 1048576:.0f} / {manifest["image_bytes"] / 1048576:.0f} MiB', end='', flush=True)
                    last_report = time.monotonic()
            print()
            total += count
        if total != manifest['image_bytes']:
            raise ValueError('Factory write map did not cover the complete image')
        os.fsync(descriptor)
        print('Reading back every factory region, including both root slots...', flush=True)
        for write in writes:
            item = manifest['payloads'][write['payload']]
            os.lseek(descriptor, write['offset'], os.SEEK_SET)
            digest, remaining = hashlib.sha256(), item['bytes']
            while remaining:
                chunk = os.read(descriptor, min(CHUNK, remaining))
                if not chunk:
                    raise OSError('Device readback ended unexpectedly')
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.hexdigest() != item['raw_sha256']:
                raise ValueError(f'SD card readback verification failed for {write["payload"]} at {write["offset"]}')
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description='Write a fully preinstalled image to an SD card (Linux)')
    parser.add_argument('device', nargs='?', help='Whole-card device, such as /dev/sdb or /dev/mmcblk0')
    parser.add_argument('--verify-only', action='store_true', help='Verify the release package without accessing a device')
    parser.add_argument('--dry-run', action='store_true', help='Check the image and target device without writing')
    parser.add_argument('--ssh-key', help='Provision pi login keys from a local OpenSSH public-key file')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1]
    try:
        manifest, image = read_manifest(directory)
        if args.verify_only:
            verify_image(image, manifest)
            print('Release package verification passed.')
            return
        if sys.platform != 'linux':
            raise ValueError('This factory writer requires Linux')
        if not args.device:
            parser.error('Specify a whole-card device path, or use --verify-only')
        key_path = str(Path(args.ssh_key).resolve(strict=True)) if args.ssh_key else None
        keys = read_ssh_keys(key_path) if key_path else None
        if keys and manifest.get('layout') != 'ab-v2':
            raise ValueError('--ssh-key requires an ab-v2 factory package')
        device = str(Path(args.device).resolve(strict=True))
        if not device.startswith('/dev/'):
            raise ValueError('Target must be under /dev')
        node = inspect_device(device, manifest['image_bytes'])
        verify_image(image, manifest)
        print(f"Target: {device}  {str(node.get('model') or '').strip()}  {int(node['size']) / 1e9:.1f} GB")
        if manifest.get('layout') == 'ab-v2':
            print(f'Minimum capacity: {manifest["image_bytes"]} bytes. STATE expands to the detected card capacity on device boot.')
        if args.dry_run:
            print('Checks passed. Nothing was written.')
            return
        if os.geteuid() != 0:
            print('Requesting sudo privileges to write the SD card...', flush=True)
            command = ['sudo', '--', sys.executable, str(Path(__file__).resolve()), device]
            if key_path: command += ['--ssh-key', key_path]
            os.execvp('sudo', command)
        with open('/dev/tty', 'r', encoding='utf-8') as tty_input, \
                open('/dev/tty', 'w', encoding='utf-8') as tty_output:
            tty_output.write(f'All data on this card will be overwritten. Type ERASE {device} to confirm: ')
            tty_output.flush()
            if tty_input.readline().strip() != f'ERASE {device}':
                raise ValueError('Cancelled. Nothing was written')
        inspect_device(device, manifest['image_bytes'])
        write_image(image, device, manifest)
        if keys:
            print('Factory image readback passed. Personalizing only STATE with the selected pi public keys.')
            provision_ssh_key(device, manifest, keys)
        subprocess.run(['blockdev', '--rereadpt', device], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('Writing and readback verification complete. Remove the SD card and insert it into the Orange Pi.')
    except (ValueError, OSError, KeyError, lzma.LZMAError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
