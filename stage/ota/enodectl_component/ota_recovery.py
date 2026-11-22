"""Explicit, local-only unsigned recovery; no persistent verification override."""
import configparser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import time
import uuid

import bootstate
import components
import state_capacity
import trial

CONFIRMATION = 'INSTALL UNVERIFIED OTA'
COMPATIBLE = 'network-exit-orangepizero3-v2'
RESERVE = 64 * 1024 * 1024


def cat_file(bundle, name, destination, limit):
    """Stream only one member, bounded on disk and in memory; execute no hooks."""
    with open(destination, 'xb') as output:
        source = subprocess.Popen(['unsquashfs', '-processors', '1', '-data-queue', '16', '-frag-queue', '16',
                                   '-no-progress', '-cat', str(bundle), name],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            subprocess.run(['head', '-c', str(limit + 1)], stdin=source.stdout,
                           stdout=output, stderr=subprocess.DEVNULL, timeout=600, check=True)
            source.stdout.close()
            result = source.wait(timeout=30)
            if result or output.tell() > limit:
                raise ValueError('Invalid or oversized OTA member: ' + name)
        finally:
            source.stdout.close()
            if source.poll() is None:
                source.kill()
            source.wait()


def extract(bundle, directory, capacity):
    manifest = directory / 'manifest.raucm'
    cat_file(bundle, 'manifest.raucm', manifest, 65536)
    parser = configparser.ConfigParser(interpolation=None)
    try: parser.read_string(manifest.read_text(encoding='ascii'))
    except configparser.Error as exc: raise ValueError('Invalid recovery manifest') from exc
    if parser.defaults() or set(parser.sections()) != {'update', 'bundle', 'image.rootfs'}:
        raise ValueError('Recovery accepts only a plain, single-rootfs bundle without hooks or handlers')
    allowed = {'update': {'compatible', 'version', 'description', 'build'},
               'bundle': {'format'}, 'image.rootfs': {'filename', 'size', 'sha256'}}
    for section, keys in allowed.items():
        if not set(parser[section]) <= keys:
            raise ValueError('Unsupported recovery manifest option')
    update, image = parser['update'], parser['image.rootfs']
    version = update.get('version', '')
    if update.get('compatible') != COMPATIBLE or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', version):
        raise ValueError('Recovery bundle board/version is incompatible')
    if parser['bundle'].get('format') != 'plain' or image.get('filename') != 'rootfs.img':
        raise ValueError('Recovery requires plain format and rootfs.img')
    size, digest = int(image.get('size', '0')), image.get('sha256', '')
    if size != capacity or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Recovery image size/checksum does not match the factory slot')
    if shutil.disk_usage(directory).free < size + RESERVE:
        raise ValueError('Insufficient STATE space to extract the recovery image')
    payload = directory / 'rootfs.img'
    cat_file(bundle, 'rootfs.img', payload, size)
    with payload.open('rb') as stream:
        if payload.stat().st_size != size or hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise ValueError('Recovery image SHA256 mismatch')
    subprocess.run(['e2fsck', '-fn', str(payload)], capture_output=True, check=True, timeout=300)
    return payload, version, digest


def locate(info, active, record):
    if active not in ('A', 'B') or record['ne_confirmed'] != active or record['ne_' + active] != '1':
        raise ValueError('Recovery requires the running confirmed slot; finish or roll back the current trial first')
    match = re.fullmatch(r'/dev/disk/by-partuuid/([0-9a-f]{8})-03', info['boot_control'])
    if info.get('layout') != 'ab-v2' or not match:
        raise ValueError('Cannot identify the factory boot-control partition')
    disk_id = match[1]
    disk, _, disk_bytes = state_capacity.discover(disk_id + '-04')
    layout = state_capacity.table(disk)
    state_capacity.validate_layout(layout, disk_bytes, disk_id + '-04')
    control = Path(info['boot_control']).resolve(strict=True)
    if control != Path(layout['partitions'][2]['node']).resolve(strict=True):
        raise ValueError('Boot control is not on the STATE card')
    index = 1 if active == 'A' else 0
    target = Path('/dev/disk/by-partuuid', f'{disk_id}-0{index + 1}').resolve(strict=True)
    if target != Path(layout['partitions'][index]['node']).resolve(strict=True):
        raise ValueError('Inactive slot is not on the STATE card')
    return target, 'B' if active == 'A' else 'A', layout['partitions'][index]['size'] * 512


def open_target(path, capacity):
    # O_EXCL on a block device rejects mounted/in-use devices. Retain this exact
    # descriptor for the write and readback instead of reopening a mutable path.
    fd = os.open(path, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode):
            raise ValueError('Recovery target must be an inactive block partition')
        actual = struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
        readonly = struct.unpack('I', fcntl.ioctl(fd, 0x125E, bytes(4)))[0]
        if actual != capacity or readonly:
            raise ValueError('Inactive partition capacity/read-only check failed')
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_verified(payload, fd, size, expected):
    written = 0
    with payload.open('rb') as source:
        while chunk := source.read(4 * 1024 * 1024):
            if written + len(chunk) > size:
                raise ValueError('Recovery image grew during writing')
            view = memoryview(chunk)
            while view:
                count = os.pwrite(fd, view, written)
                if count <= 0:
                    raise OSError('Short recovery write')
                view = view[count:]
                written += count
    if written != size:
        raise ValueError('Recovery image was truncated during writing')
    os.fsync(fd)
    digest, position = hashlib.sha256(), 0
    while position < size:
        chunk = os.pread(fd, min(4 * 1024 * 1024, size - position), position)
        if not chunk:
            raise OSError('Short recovery readback')
        digest.update(chunk)
        position += len(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('Recovery slot readback SHA256 mismatch')


def install(bundle, info, active, before, state_directory):
    target, slot, capacity = locate(info, active, before)
    payload, version, digest = extract(bundle, bundle.parent, capacity)
    audit = state_directory / 'ota-recovery' / (uuid.uuid4().hex + '.json')
    record = {'signature_verified': False, 'target_slot': slot, 'version': version,
              'image_sha256': digest, 'time': int(time.time()), 'status': 'prepared'}
    with bundle.open('rb') as stream:
        record['bundle_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    components.save(audit, json.dumps(record) + '\n')
    control = Path(info['boot_control']).resolve(strict=True)
    fd = open_target(target, capacity)
    try:
        def invalidate(value):
            if value != before:
                raise ValueError('Boot state changed during recovery preparation')
            value.update({'ne_' + slot: '0', 'ne_pending': 'none', 'ne_left': '0'})
            return value
        with trial.decision_lock():
            bootstate.update(control, invalidate)
            marked = bootstate.load(control)
        write_verified(payload, fd, capacity, digest)
        def activate(value):
            if value != marked:
                raise ValueError('Boot state changed during recovery writing; target remains unconfirmed')
            value.update({'ne_' + slot: '1', 'ne_pending': slot, 'ne_left': '3'})
            return value
        with trial.decision_lock():
            bootstate.update(control, activate)
        record['status'] = 'pending-trial'
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        os.close(fd)
        components.save(audit, json.dumps(record) + '\n')
