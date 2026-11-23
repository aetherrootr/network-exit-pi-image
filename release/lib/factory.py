"""Validated component-based factory transport; no block-device operations."""
import hashlib
import lzma
from pathlib import Path
import stat
import struct

CHUNK = 4 * 1024 * 1024
MIB = 1024 * 1024
PREFIX_BYTES = 4 * MIB
NAMES = ('boot-prefix', 'rootfs', 'boot-control', 'state')


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def geometry(root_bytes, state_bytes):
    if (type(root_bytes) is not int or root_bytes < MIB or root_bytes > 4092 * MIB
            or root_bytes % MIB or type(state_bytes) is not int
            or state_bytes < MIB or state_bytes % MIB):
        raise ValueError('Invalid factory partition sizes')
    sizes = (PREFIX_BYTES, root_bytes, root_bytes, MIB, state_bytes)
    offset = 0
    writes = []
    for name, size in zip(('boot-prefix', 'rootfs', 'rootfs', 'boot-control', 'state'), sizes):
        writes.append({'payload': name, 'offset': offset})
        offset += size
    if offset >= 2 ** 41:
        raise ValueError('Factory layout exceeds the supported MBR capacity')
    return offset, writes


def validate(directory, manifest):
    """Require complete, contiguous A/B coverage and fixed safe payload paths."""
    if (not isinstance(manifest, dict) or manifest.get('format') != 2 or manifest.get('kind') != 'factory'
            or manifest.get('layout') != 'ab-v2' or manifest.get('board') != 'orangepizero3'
            or manifest.get('arch') != 'arm64'):
        raise ValueError('Unsupported component factory manifest')
    payloads = manifest.get('payloads')
    if not isinstance(payloads, dict) or set(payloads) != set(NAMES):
        raise ValueError('Factory must contain exactly four distinct payloads')
    for name in NAMES:
        item = payloads[name]
        if (not isinstance(item, dict) or set(item) != {'file', 'bytes', 'sha256', 'raw_sha256'}
                or item['file'] != f'payload/{name}.img.xz'):
            raise ValueError('Invalid factory payload path or metadata')
        for field in ('sha256', 'raw_sha256'):
            value = item[field]
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Invalid factory payload checksum')
        if type(item['bytes']) is not int:
            raise ValueError('Invalid factory payload size')
    total, writes = geometry(payloads['rootfs']['bytes'], payloads['state']['bytes'])
    if (payloads['boot-prefix']['bytes'] != PREFIX_BYTES or payloads['boot-control']['bytes'] != MIB
            or type(manifest.get('image_bytes')) is not int or manifest['image_bytes'] != total
            or manifest.get('writes') != writes):
        raise ValueError('Factory write map does not match its A/B partition geometry')
    # Refuse symlinked payloads even when their contents happen to match a hash.
    directory = Path(directory)
    if (directory / 'payload').is_symlink():
        raise ValueError('Factory payload directory must not be a symlink')
    for item in payloads.values():
        path = directory / item['file']
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Factory payload must be a regular file')
    return writes


def chunks(directory, item):
    """Bound decompression and check both transport and raw bytes on every pass."""
    path = Path(directory) / item['file']
    if checksum(path) != item['sha256']:
        raise ValueError('Compressed factory payload SHA256 mismatch')
    count, digest = 0, hashlib.sha256()
    with lzma.open(path, 'rb') as stream:
        while chunk := stream.read(min(CHUNK, item['bytes'] - count + 1)):
            count += len(chunk)
            if count > item['bytes']:
                raise ValueError('Factory payload exceeds its declared size')
            digest.update(chunk)
            yield chunk
    if count != item['bytes'] or digest.hexdigest() != item['raw_sha256']:
        raise ValueError('Factory payload decompressed size or SHA256 mismatch')


def verify(directory, manifest):
    validate(directory, manifest)
    for name in NAMES:
        prefix = bytearray()
        for chunk in chunks(directory, manifest['payloads'][name]):
            if name == 'boot-prefix' and len(prefix) < 512:
                prefix.extend(chunk[:512 - len(prefix)])
        if name == 'boot-prefix':
            verify_mbr(prefix, manifest)


def verify_mbr(prefix, manifest):
    if len(prefix) < 512 or prefix[510:512] != b'\x55\xaa':
        raise ValueError('Factory boot prefix has no valid MBR signature')
    payloads = manifest['payloads']
    for index, write in enumerate(manifest['writes'][1:]):
        entry = prefix[446 + 16 * index:462 + 16 * index]
        start, sectors = struct.unpack_from('<II', entry, 8)
        size = payloads[write['payload']]['bytes']
        expected_boot = 0x80 if index < 2 else 0
        if (entry[0] != expected_boot or entry[4] != 0x83
                or start * 512 != write['offset'] or sectors * 512 != size):
            raise ValueError('Factory MBR differs from the declared write map')
