"""Small, valid component transports for release/writer integration checks."""
import hashlib
import json
import lzma
from pathlib import Path
import struct

from release.lib import factory


def component_fixture(output, rootfs=None):
    rootfs = rootfs if rootfs is not None else b'system-slot\0'.ljust(factory.MIB, b'\0')
    total, writes = factory.geometry(len(rootfs), factory.MIB)
    prefix = bytearray(factory.PREFIX_BYTES)
    prefix[:17] = b'BOOTLOADER-BYTES!'
    prefix[440:444] = struct.pack('<I', 0x1234abcd)
    prefix[510:512] = b'\x55\xaa'
    sizes = {'boot-prefix': factory.PREFIX_BYTES, 'rootfs': len(rootfs),
             'boot-control': factory.MIB, 'state': factory.MIB}
    for index, write in enumerate(writes[1:]):
        struct.pack_into('<B3sB3sII', prefix, 446 + 16 * index,
                         0x80 if index < 2 else 0, b'\xff' * 3, 0x83, b'\xff' * 3,
                         write['offset'] // 512, sizes[write['payload']] // 512)
    raw = {'boot-prefix': bytes(prefix), 'rootfs': rootfs,
           'boot-control': b'initial-control\0'.ljust(factory.MIB, b'\0'),
           'state': b'initial-state\0'.ljust(factory.MIB, b'\0')}
    directory = Path(output) / 'factory'
    (directory / 'payload').mkdir(parents=True)
    manifest = {'format': 2, 'kind': 'factory', 'layout': 'ab-v2', 'board': 'orangepizero3',
                'arch': 'arm64', 'image_bytes': total, 'writes': writes, 'payloads': {}}
    for name, content in raw.items():
        filename = f'payload/{name}.img.xz'
        compressed = lzma.compress(content, preset=3)
        (directory / filename).write_bytes(compressed)
        manifest['payloads'][name] = {'file': filename, 'bytes': len(content),
                                      'sha256': hashlib.sha256(compressed).hexdigest(),
                                      'raw_sha256': hashlib.sha256(content).hexdigest()}
    (directory / 'layout.json').write_text(json.dumps(manifest))
    return directory, manifest, raw
