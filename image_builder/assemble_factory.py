#!/usr/bin/env python3
"""Prepare shared-rootfs factory components or an optional regular A/B image."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from release.lib import factory as transport

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstate', ROOT / 'stage/ota/enodectl_component/bootstate.py')
bootstate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstate)


def assemble(base, rootfs, output, state_mib=2048):
    base, rootfs, output = map(Path, [base, rootfs, output])
    if not base.is_file() or not rootfs.is_file() or output.exists():
        raise ValueError('Inputs must be regular files and the output must not exist')
    prefix_size = 8192 * 512
    size = rootfs.stat().st_size
    if size % (1024 * 1024):
        raise ValueError('Root filesystem must be MiB-aligned')
    state_bytes = state_mib * 1024 * 1024
    starts = [prefix_size, prefix_size + size, prefix_size + 2 * size,
              prefix_size + 2 * size + 1024 * 1024]
    total = starts[3] + state_bytes
    with base.open('rb') as src:
        prefix = src.read(prefix_size)
    if len(prefix) != prefix_size or prefix[510:512] != b'\x55\xaa':
        raise ValueError('Invalid base boot prefix')
    with output.open('xb') as target:
        target.write(prefix)
        target.truncate(total)
    disk_id = struct.unpack_from('<I', prefix, 440)[0]
    table = f'label: dos\nlabel-id: 0x{disk_id:08x}\nunit: sectors\n'
    for index, (start, length) in enumerate(zip(starts, [size, size, 1024 * 1024, state_bytes])):
        table += f'start={start // 512}, size={length // 512}, type=83' + (', bootable' if index < 2 else '') + '\n'
    subprocess.run(['sfdisk', '--no-reread', '--force', str(output)], input=table,
                   text=True, check=True, stdout=subprocess.DEVNULL)
    with output.open('rb') as target:
        revised = target.read(prefix_size)
    if revised[:446] != prefix[:446] or revised[510:] != prefix[510:]:
        raise ValueError('Assembler changed boot bytes outside the partition records')
    with output.open('r+b') as target:
        for start in starts[:2]:
            target.seek(start)
            with rootfs.open('rb') as src:
                shutil.copyfileobj(src, target, 4 * 1024 * 1024)
        with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
            state = Path(tmp) / 'state.img'
            with state.open('wb') as stream: stream.truncate(state_bytes)
            subprocess.run(['mkfs.ext4', '-q', '-F', '-m', '0', '-L', 'NETWORK_STATE', str(state)], check=True)
            target.seek(starts[3])
            with state.open('rb') as src: shutil.copyfileobj(src, target, 4 * 1024 * 1024)
        record = bootstate.encode(bootstate.initial())
        for bank in (0, 1):
            target.seek(starts[2] + bank * bootstate.STRIDE)
            target.write(record)
        target.flush()
        os.fsync(target.fileno())
    return total


def prepare_components(base, rootfs, output, state_mib=2048):
    """Prepare small boot/STATE templates; rootfs stays in its original file."""
    base, rootfs, output = map(Path, (base, rootfs, output))
    if not base.is_file() or not rootfs.is_file() or output.exists():
        raise ValueError('Inputs must be regular files and the output must not exist')
    sizes = {'boot-prefix': transport.PREFIX_BYTES, 'rootfs': rootfs.stat().st_size,
             'boot-control': transport.MIB, 'state': state_mib * transport.MIB}
    total, writes = transport.geometry(sizes['rootfs'], sizes['state'])
    with base.open('rb') as stream:
        prefix = stream.read(transport.PREFIX_BYTES)
    if len(prefix) != transport.PREFIX_BYTES or prefix[510:512] != b'\x55\xaa':
        raise ValueError('Invalid base boot prefix')
    output.mkdir()
    # sfdisk operates on a sparse temporary file. No duplicate system payloads
    # are written: only its revised first 4 MiB are retained.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        disk = Path(tmp) / 'layout.img'
        with disk.open('xb') as stream:
            stream.write(prefix)
            stream.truncate(total)
        disk_id = struct.unpack_from('<I', prefix, 440)[0]
        table = f'label: dos\nlabel-id: 0x{disk_id:08x}\nunit: sectors\n'
        for index, write in enumerate(writes[1:]):
            table += (f'start={write["offset"] // 512}, size={sizes[write["payload"]] // 512}, type=83'
                      + (', bootable' if index < 2 else '') + '\n')
        subprocess.run(['sfdisk', '--no-reread', '--force', str(disk)], input=table,
                       text=True, check=True, stdout=subprocess.DEVNULL)
        with disk.open('rb') as stream:
            revised = stream.read(transport.PREFIX_BYTES)
        if revised[:446] != prefix[:446] or revised[510:] != prefix[510:]:
            raise ValueError('Assembler changed boot bytes outside the partition records')
        (output / 'boot-prefix.img').write_bytes(revised)
    control = bytearray(transport.MIB)
    record = bootstate.encode(bootstate.initial())
    for bank in (0, 1):
        control[bank * bootstate.STRIDE:bank * bootstate.STRIDE + len(record)] = record
    (output / 'boot-control.img').write_bytes(control)
    state = output / 'state.img'
    with state.open('xb') as stream:
        stream.truncate(sizes['state'])
    subprocess.run(['mkfs.ext4', '-q', '-F', '-m', '0', '-L', 'NETWORK_STATE', str(state)], check=True)
    manifest = {'format': 2, 'kind': 'factory', 'board': 'orangepizero3', 'arch': 'arm64',
                'layout': 'ab-v2', 'image_bytes': total, 'writes': writes,
                'payloads': {name: {'file': f'payload/{name}.img.xz', 'bytes': sizes[name]}
                             for name in transport.NAMES}}
    transport.verify_mbr(revised, manifest)
    (output / 'layout.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def compress_components(directory, rootfs):
    directory, rootfs = Path(directory), Path(rootfs)
    manifest = json.loads((directory / 'layout.json').read_text())
    (directory / 'payload').mkdir()
    for name in transport.NAMES:
        source = rootfs if name == 'rootfs' else directory / f'{name}.img'
        item = manifest['payloads'][name]
        if source.stat().st_size != item['bytes']:
            raise ValueError('Factory source size changed before compression')
        item['raw_sha256'] = transport.checksum(source)
        destination = directory / item['file']
        with destination.open('xb') as target:
            subprocess.run(['xz', '-T2', '-3', '-c', str(source)], stdout=target, check=True)
        item['sha256'] = transport.checksum(destination)
    transport.verify(directory, manifest)
    (directory / 'layout.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in ('boot-prefix', 'boot-control', 'state'):
        (directory / f'{name}.img').unlink()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--base')
    source.add_argument('--compress', help='Compress an already prepared component directory')
    parser.add_argument('--rootfs', required=True)
    parser.add_argument('--output')
    parser.add_argument('--components', action='store_true', help='Prepare shared-rootfs transport instead of a raw disk')
    args = parser.parse_args()
    if args.compress:
        if args.output or args.components:
            parser.error('--compress does not accept --output or --components')
        result = compress_components(args.compress, args.rootfs)
    else:
        if not args.output:
            parser.error('--base requires --output')
        operation = prepare_components if args.components else assemble
        result = operation(args.base, args.rootfs, args.output)
    print(json.dumps(result))


if __name__ == '__main__': main()
